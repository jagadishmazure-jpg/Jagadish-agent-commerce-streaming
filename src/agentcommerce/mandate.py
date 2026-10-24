"""Signed spend mandates and cart approvals.

Modelled on the public ideas behind agent-payment protocols (an "intent" mandate that bounds what an
agent may spend, and a "cart" mandate where the user approves exact contents), described generically.

* SpendMandate: user grants the agent a cap per purchase, a total cap, a merchant allowlist and an
  expiry. Signed by the user's key (HMAC-SHA256 here; a real system would use device-bound keys).
* CartApproval: user approves one exact cart (hash of merchant + lines + total) under one mandate,
  with a single-use nonce and a short expiry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass, field

from .money import cents
from .pii import SafeError


def _sign(key: bytes, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class SpendMandate:
    mandate_id: str
    user_id: str
    per_purchase_cap: int
    total_cap: int
    merchants: tuple[str, ...]
    expires_at: float
    currency: str = "USD"
    signature: str = ""

    def payload(self) -> dict:
        d = asdict(self)
        d.pop("signature")
        d["merchants"] = list(self.merchants)
        return d

    def signed(self, key: bytes) -> SpendMandate:
        return SpendMandate(**{**asdict(self), "signature": _sign(key, self.payload())})

    def verify(self, key: bytes, now: float) -> None:
        if not hmac.compare_digest(self.signature, _sign(key, self.payload())):
            raise SafeError("mandate signature invalid")
        if now >= self.expires_at:
            raise SafeError("mandate expired")


def new_mandate(
    key: bytes, user_id: str, per_purchase_cap: int, total_cap: int, merchants: list[str], now: float, ttl_s: float = 3600
) -> SpendMandate:
    per, total = cents(per_purchase_cap, "per_purchase_cap"), cents(total_cap, "total_cap")
    if per > total:
        raise SafeError("per_purchase_cap cannot exceed total_cap")
    if ttl_s <= 0:
        raise SafeError("ttl must be positive")
    if not merchants:
        raise SafeError("merchant allowlist must not be empty")
    m = SpendMandate(
        mandate_id="md_" + secrets.token_hex(6),
        user_id=user_id,
        per_purchase_cap=per,
        total_cap=total,
        merchants=tuple(sorted(set(merchants))),
        expires_at=now + ttl_s,
    )
    return m.signed(key)


@dataclass(frozen=True)
class Line:
    sku: str
    name: str
    unit_cents: int
    qty: int

    def __post_init__(self) -> None:
        cents(self.unit_cents, "unit price")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty <= 0:
            raise SafeError("quantity must be a positive whole number")


@dataclass(frozen=True)
class Cart:
    merchant: str
    lines: tuple[Line, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return sum(ln.unit_cents * ln.qty for ln in self.lines)

    def digest(self) -> str:
        body = json.dumps(
            {"merchant": self.merchant, "lines": [asdict(ln) for ln in self.lines], "total": self.total},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass(frozen=True)
class CartApproval:
    mandate_id: str
    cart_digest: str
    amount: int
    nonce: str
    expires_at: float
    signature: str = ""

    def payload(self) -> dict:
        d = asdict(self)
        d.pop("signature")
        return d

    def verify(self, key: bytes, now: float) -> None:
        if not hmac.compare_digest(self.signature, _sign(key, self.payload())):
            raise SafeError("approval signature invalid")
        if now >= self.expires_at:
            raise SafeError("approval expired")


def approve_cart(key: bytes, mandate: SpendMandate, cart: Cart, now: float, ttl_s: float = 300) -> CartApproval:
    """What the user's device does when they press Approve on the exact cart shown."""
    a = CartApproval(
        mandate_id=mandate.mandate_id,
        cart_digest=cart.digest(),
        amount=cart.total,
        nonce=secrets.token_hex(8),
        expires_at=now + ttl_s,
    )
    return CartApproval(**{**asdict(a), "signature": _sign(key, a.payload())})
