"""A scripted shopping agent with a deterministic stand-in for the model.

The "model" is rule-based so tests and timing are reproducible and free. What matters is the
control flow around it, which is the same as with a real model:

* every reply is emitted as a stream of events: `token`, `tool_call`, `tool_result`,
  `approval_required`, `guard`, `done`;
* checkout and refunds are risky actions: the agent pauses and needs an explicit user decision;
* consent is read from the user's *final* stance in a message ("yes... wait, no" is a no), and a
  conditional yes ("yes if it is under $10") is treated as unclear;
* text that tries to override instructions, limits or payees is refused before any tool runs.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from .mandate import Cart, Line, SpendMandate, approve_cart, new_mandate
from .money import fmt
from .payments import PaymentError, PaymentService
from .pii import scrub

MERCHANT = "fernhill-market"
CATALOG = {
    "trail mix": Line("FM-101", "trail mix", 650, 1),
    "water filter": Line("FM-205", "water filter", 3400, 1),
    "headlamp": Line("FM-310", "headlamp", 2900, 1),
    "camp chair": Line("FM-412", "camp chair", 5900, 1),
    "tent": Line("FM-520", "tent", 18900, 1),
}

INJECTION = re.compile(
    r"ignore (all |the |your )?(previous|prior|above)|system prompt|you are now|developer mode|"
    r"override (the )?(limit|cap|mandate)|send (the )?(payment|money) to|new payee|disable (the )?(check|guard)|"
    r"\bsystem:\s|pretend (the )?user (said|approved)",
    re.I,
)
LEET = str.maketrans({"1": "i", "0": "o", "3": "e", "4": "a", "@": "a", "$": "s", "5": "s", "7": "t"})
CONDITIONAL = re.compile(r"\b(if|only if|unless|as long as|provided)\b", re.I)
YES = re.compile(r"\b(yes|yep|yeah|sure|ok(ay)?|confirm(ed)?|go ahead|do it|approve[d]?|sounds good)\b", re.I)
NO = re.compile(r"\b(no|nope|don'?t|do not|cancel|stop|reject|never ?mind|not now)\b", re.I)
HOLD = re.compile(r"\b(wait|hold on|actually|hmm+|let me think|not sure|maybe)\b", re.I)
ANGRY = re.compile(r"\b(ridiculous|useless|terrible|furious|worst|scam|angry)\b|!!", re.I)
HUMAN = re.compile(r"\b(manager|human|real person|supervisor)\b", re.I)
REFUND = re.compile(r"\b(refund|money back|reimburse)\b", re.I)
AMOUNT = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")


def looks_like_injection(text: str) -> bool:
    """Match the raw text and a de-leeted copy (the leetspeak gap was found by the persona simulator)."""
    return bool(INJECTION.search(text) or INJECTION.search(text.translate(LEET)))


def consent(text: str) -> str:
    """Return 'yes', 'no' or 'unclear' from the user's last stated position in the message."""
    marks: list[tuple[int, str]] = []
    for rx, kind in ((YES, "yes"), (NO, "no"), (HOLD, "hold")):
        marks += [(m.start(), kind) for m in rx.finditer(text)]
    if not marks:
        return "unclear"
    pos, last = sorted(marks)[-1]
    if last == "yes" and CONDITIONAL.search(text[pos:]):
        return "unclear"  # a conditional yes is not consent (found by the persona simulator)
    return "unclear" if last == "hold" else last


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)


@dataclass
class Session:
    service: PaymentService
    mandate: SpendMandate
    card_token: str
    pending: dict | None = None
    last_payment: str | None = None
    turn: int = 0
    log: list[str] = field(default_factory=list)


def new_session(user_key: bytes, now: float | None = None, per_cap: int = 20000, total_cap: int = 40000) -> Session:
    """`now` freezes the payment clock (tests and the persona simulator); default is the real clock."""
    if now is None:
        now, svc = time.time(), PaymentService(user_key=user_key)
    else:
        fixed = now
        svc = PaymentService(user_key=user_key, clock=lambda: fixed)
    mandate = new_mandate(user_key, "user-1", per_cap, total_cap, [MERCHANT, "larkspur-books"], now)
    token = svc.tokenize("4242 4242 4242 4242")  # public test card number
    return Session(svc, mandate, token)


def _say(text: str) -> Iterator[Event]:
    for word in text.split(" "):
        yield Event("token", {"text": " " + word})


def _find_item(text: str) -> str | None:
    low = text.lower()
    return next((name for name in CATALOG if name in low), None)


class CommerceAgent:
    def __init__(self, session: Session) -> None:
        self.s = session

    # -- decisions on a pending risky action (from chat text or from the UI buttons) -----------------
    def decide(self, approved: bool) -> Iterator[Event]:
        p = self.s.pending
        if p is None:
            yield from _say("There is nothing waiting for approval.")
            yield Event("done")
            return
        self.s.pending = None
        if not approved:
            yield Event("tool_result", {"tool": p["tool"], "status": "rejected_by_user"})
            yield from _say("Okay, I did not do it. Nothing was charged or refunded.")
            yield Event("done")
            return
        svc, m = self.s.service, self.s.mandate
        try:
            if p["tool"] == "checkout":
                cart: Cart = p["cart"]
                approval = approve_cart(svc.user_key, m, cart, svc.clock())
                yield Event("tool_call", {"tool": "checkout", "amount": cart.total, "merchant": cart.merchant})
                r = svc.checkout(mandate=m, cart=cart, approval=approval, card_token=self.s.card_token, idempotency_key=p["idem"])
                self.s.last_payment = r.payment_id
                yield Event("tool_result", {"tool": "checkout", "status": "captured", "payment_id": r.payment_id})
                yield from _say(f"Done. Paid {fmt(r.amount)} with the card ending {svc.last4(self.s.card_token)}.")
            else:
                yield Event("tool_call", {"tool": "refund", "amount": p["amount"]})
                r = svc.refund(p["payment_id"], p["amount"], p["idem"])
                yield Event("tool_result", {"tool": "refund", "status": r.status})
                yield from _say(f"Refunded {fmt(p['amount'])}.")
        except PaymentError as e:
            yield Event("tool_result", {"tool": p["tool"], "status": "refused", "code": e.code})
            yield from _say(f"The payment service refused that ({e.code}). Nothing was charged.")
        yield Event("done")

    # -- one user message -------------------------------------------------------------------------
    def respond(self, text: str) -> Iterator[Event]:
        self.s.turn += 1
        self.s.log.append(scrub(text))
        if looks_like_injection(text):
            yield Event("guard", {"rule": "prompt_injection"})
            yield from _say("I can't follow instructions that change my limits, payees or rules. Nothing was done.")
            if self.s.pending:
                yield from _say("Your earlier request is still waiting for a clear yes or no.")
            yield Event("done")
            return
        if self.s.pending is not None:
            c = consent(text)
            if c in ("yes", "no"):
                yield from self.decide(c == "yes")
                return
            yield from _say(f"Just to be sure: should I go ahead with the {self.s.pending['summary']}? Please say yes or no.")
            yield Event("approval_required", self._pending_view())
            yield Event("done")
            return
        if HUMAN.search(text):
            yield Event("tool_call", {"tool": "handoff_to_human"})
            yield from _say("I'm sorry about this. I've asked a person from the support team to take over.")
            yield Event("done")
            return
        if REFUND.search(text):
            yield from self._refund(text)
            return
        item = _find_item(text)
        if item:
            yield from self._buy(item)
            return
        prefix = "I'm sorry for the trouble. " if ANGRY.search(text) else ""
        yield from _say(f"{prefix}Which item would you like? I can help with: {', '.join(CATALOG)}.")
        yield Event("done")

    def _pending_view(self) -> dict:
        p = self.s.pending or {}
        return {"tool": p.get("tool"), "summary": p.get("summary"), "amount": p.get("amount")}

    def _buy(self, item: str) -> Iterator[Event]:
        line = CATALOG[item]
        yield Event("tool_call", {"tool": "search_catalog", "query": item})
        yield Event("tool_result", {"tool": "search_catalog", "sku": line.sku, "price": line.unit_cents})
        cart = Cart(MERCHANT, (line,))
        if cart.total > self.s.mandate.per_purchase_cap:
            yield from _say(f"The {item} costs {fmt(cart.total)}, above your per-purchase limit. I can't buy it.")
            yield Event("done")
            return
        summary = f"purchase of 1 {item} for {fmt(cart.total)} from {MERCHANT}"
        self.s.pending = {
            "tool": "checkout",
            "cart": cart,
            "amount": cart.total,
            "summary": summary,
            "idem": f"chk-{self.s.mandate.mandate_id}-{self.s.turn}",
        }
        yield from _say(f"I found it. Shall I place a {summary}?")
        yield Event("approval_required", self._pending_view())
        yield Event("done")

    def _refund(self, text: str) -> Iterator[Event]:
        pid = self.s.last_payment
        if pid is None:
            yield from _say("I can't find a purchase from this conversation to refund.")
            yield Event("done")
            return
        r = self.s.service.receipts[pid]
        left = r.amount - r.refunded
        m = AMOUNT.search(text)
        asked = round(float(m.group(1)) * 100) if m else left
        if asked > left:
            yield from _say(f"I can refund at most {fmt(left)}, the amount still on that order.")
            asked = left
        if asked == 0:
            yield from _say("That order is already fully refunded.")
            yield Event("done")
            return
        self.s.pending = {
            "tool": "refund",
            "payment_id": pid,
            "amount": asked,
            "summary": f"refund of {fmt(asked)}",
            "idem": f"ref-{pid}-{self.s.turn}",
        }
        yield from _say(f"Shall I issue a refund of {fmt(asked)}?")
        yield Event("approval_required", self._pending_view())
        yield Event("done")


def reply_text(events: list[Event]) -> str:
    return "".join(e.data["text"] for e in events if e.type == "token").strip()
