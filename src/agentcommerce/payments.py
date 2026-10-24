"""Mock payment service: card vault, idempotent checkout, refunds, fraud and replay checks.

Nothing here talks to a real processor. The rules mirror what a real agent-checkout integration
needs: the agent never holds a card number (only a vault token), every capture is bound to a
signed mandate and a signed approval of the exact cart, approvals are single-use, and retries with
the same idempotency key return the original receipt instead of charging twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .mandate import Cart, CartApproval, SpendMandate
from .money import cents
from .pii import SafeError, get_logger, luhn_ok

log = get_logger("agentcommerce.payments")


class PaymentError(SafeError):
    """Raised for every refused payment; message is scrubbed and carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class Receipt:
    payment_id: str
    mandate_id: str
    merchant: str
    amount: int
    card_token: str
    refunded: int = 0
    status: str = "captured"


@dataclass
class FraudPolicy:
    max_checkouts_per_window: int = 3
    window_s: float = 60.0
    spike_multiple: float = 5.0  # flag a purchase this many times above the user's median
    min_history_for_spike: int = 3

    def __post_init__(self) -> None:
        for name in ("max_checkouts_per_window", "window_s", "spike_multiple", "min_history_for_spike"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
                raise SafeError(f"{name} must be a non-negative number")


@dataclass
class PaymentService:
    user_key: bytes
    clock: Callable[[], float] = time.time
    fraud: FraudPolicy = field(default_factory=FraudPolicy)
    receipts: dict[str, Receipt] = field(default_factory=dict)
    _vault: dict[str, str] = field(default_factory=dict)
    _idem: dict[str, tuple[str, str]] = field(default_factory=dict)
    _used_nonces: set[str] = field(default_factory=set)
    _spent: dict[str, int] = field(default_factory=dict)
    _history: dict[str, list[tuple[float, int]]] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    # ---- vault -------------------------------------------------------------------------------
    def tokenize(self, card_number: str) -> str:
        digits = re.sub(r"[ -]", "", str(card_number))
        if not digits.isdigit() or not 13 <= len(digits) <= 19 or not luhn_ok(digits):
            # Never echo the input: a mistyped card fails Luhn, so scrub() would not mask it (bug caught by a test).
            raise PaymentError("card_invalid", "card number rejected")
        token = "tok_" + secrets.token_hex(8)
        self._vault[token] = digits
        log.info("card vaulted as %s ending %s", token, digits[-4:])
        return token

    def last4(self, token: str) -> str:
        if token not in self._vault:
            raise PaymentError("token_unknown", "unknown card token")
        return self._vault[token][-4:]

    # ---- checkout ----------------------------------------------------------------------------
    def _event(self, kind: str, **kw: object) -> None:
        self.events.append({"kind": kind, **kw})
        log.info("payment event %s %s", kind, kw)

    def checkout(
        self, *, mandate: SpendMandate, cart: Cart, approval: CartApproval, card_token: str, idempotency_key: str
    ) -> Receipt:
        if not idempotency_key or len(idempotency_key) > 128:
            raise PaymentError("idempotency_key", "idempotency key required (1-128 chars)")
        fingerprint = hashlib.sha256(
            json.dumps([mandate.mandate_id, cart.digest(), card_token], separators=(",", ":")).encode()
        ).hexdigest()
        if idempotency_key in self._idem:
            prev_fp, pid = self._idem[idempotency_key]
            if prev_fp != fingerprint:
                self._event("idempotency_conflict", key=idempotency_key)
                raise PaymentError("idempotency_conflict", "key reused with a different request")
            self._event("idempotent_replay", payment_id=pid)
            return self.receipts[pid]

        now = self.clock()
        try:
            mandate.verify(self.user_key, now)
            approval.verify(self.user_key, now)
        except SafeError as e:
            self._event("rejected", reason=str(e))
            raise PaymentError("auth", str(e)) from None
        if approval.nonce in self._used_nonces:
            self._event("replay_blocked", nonce=approval.nonce)
            raise PaymentError("replay", "approval already used")
        if approval.mandate_id != mandate.mandate_id:
            raise PaymentError("mismatch", "approval is for a different mandate")
        if approval.cart_digest != cart.digest() or approval.amount != cart.total:
            self._event("cart_tampered", merchant=cart.merchant)
            raise PaymentError("cart_changed", "cart differs from what the user approved")
        if cart.merchant not in mandate.merchants:
            self._event("merchant_blocked", merchant=cart.merchant)
            raise PaymentError("merchant", "merchant is not on the mandate allowlist")
        amount = cents(cart.total, "cart total")
        if amount == 0:
            raise PaymentError("empty", "cart total is zero")
        if amount > mandate.per_purchase_cap:
            raise PaymentError("cap", "purchase exceeds the per-purchase cap")
        if self._spent.get(mandate.mandate_id, 0) + amount > mandate.total_cap:
            raise PaymentError("cap", "purchase would exceed the mandate's total cap")
        if card_token not in self._vault:
            raise PaymentError("token_unknown", "unknown card token")
        self._fraud_check(mandate.user_id, amount, now)

        self._used_nonces.add(approval.nonce)
        pid = "pay_" + secrets.token_hex(6)
        r = Receipt(pid, mandate.mandate_id, cart.merchant, amount, card_token)
        self.receipts[pid] = r
        self._idem[idempotency_key] = (fingerprint, pid)
        self._spent[mandate.mandate_id] = self._spent.get(mandate.mandate_id, 0) + amount
        self._history.setdefault(mandate.user_id, []).append((now, amount))
        self._event("captured", payment_id=pid, amount=amount, merchant=cart.merchant)
        return r

    def _fraud_check(self, user_id: str, amount: int, now: float) -> None:
        hist = self._history.get(user_id, [])
        recent = [t for t, _ in hist if now - t < self.fraud.window_s]
        if len(recent) >= self.fraud.max_checkouts_per_window:
            self._event("fraud_velocity", user=user_id)
            raise PaymentError("fraud_velocity", "too many checkouts in a short window")
        if len(hist) >= self.fraud.min_history_for_spike:
            amounts = sorted(a for _, a in hist)
            median = amounts[len(amounts) // 2]
            if median and amount > self.fraud.spike_multiple * median:
                self._event("fraud_spike", user=user_id, amount=amount)
                raise PaymentError("fraud_spike", "amount far above this user's usual spend; needs review")

    # ---- refunds -----------------------------------------------------------------------------
    def refund(self, payment_id: str, amount: int, idempotency_key: str) -> Receipt:
        amount = cents(amount, "refund amount")
        if payment_id not in self.receipts:
            raise PaymentError("unknown_payment", "no such payment")
        key = "refund:" + idempotency_key
        fp = f"{payment_id}:{amount}"
        if key in self._idem:
            if self._idem[key][0] != fp:
                raise PaymentError("idempotency_conflict", "key reused with a different refund")
            return self.receipts[payment_id]
        r = self.receipts[payment_id]
        if amount == 0:
            raise PaymentError("refund_zero", "refund amount must be positive")
        if r.refunded + amount > r.amount:
            self._event("refund_over_capture", payment_id=payment_id, asked=amount)
            raise PaymentError("refund_too_large", "refund exceeds the captured amount")
        r.refunded += amount
        r.status = "refunded" if r.refunded == r.amount else "partially_refunded"
        self._spent[r.mandate_id] -= amount
        self._idem[key] = (fp, payment_id)
        self._event("refunded", payment_id=payment_id, amount=amount)
        return r

    def captured_total(self) -> int:
        return sum(r.amount - r.refunded for r in self.receipts.values())
