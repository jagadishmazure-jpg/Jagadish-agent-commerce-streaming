"""HTTP 402 "payment required" handshake for paid agent resources, described generically.

A paid endpoint answers an unpaid request with status 402 and a quote (price, merchant, quote id,
expiry). The agent pays under its mandate and retries with a payment proof header. The server
checks the proof is signed, unexpired, unused and for this exact quote before serving the resource.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass

from .money import cents
from .pii import SafeError


@dataclass(frozen=True)
class Quote:
    quote_id: str
    resource: str
    merchant: str
    amount: int
    expires_at: float


def _mac(key: bytes, payload: dict) -> str:
    return hmac.new(key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()


class PaywalledResource:
    def __init__(self, key: bytes, merchant: str, price: int, resource: str) -> None:
        self.key, self.merchant, self.price, self.resource = key, merchant, cents(price, "price"), resource
        self.quotes: dict[str, Quote] = {}
        self.spent: set[str] = set()

    def quote(self, now: float, ttl_s: float = 120) -> Quote:
        q = Quote("q_" + secrets.token_hex(6), self.resource, self.merchant, self.price, now + ttl_s)
        self.quotes[q.quote_id] = q
        return q

    def redeem(self, proof: dict, now: float) -> str:
        sig = proof.get("signature", "")
        body = {k: v for k, v in proof.items() if k != "signature"}
        if not hmac.compare_digest(str(sig), _mac(self.key, body)):
            raise SafeError("payment proof signature invalid")
        q = self.quotes.get(str(body.get("quote_id")))
        if q is None:
            raise SafeError("unknown quote")
        if now >= q.expires_at:
            raise SafeError("quote expired")
        if body.get("amount") != q.amount or body.get("merchant") != q.merchant:
            raise SafeError("proof does not match the quote")
        if q.quote_id in self.spent:
            raise SafeError("payment proof already used")
        self.spent.add(q.quote_id)
        return f"contents of {self.resource}"


def pay_quote(key: bytes, q: Quote, allowlist: set[str], cap: int) -> dict:
    """Agent side: refuse quotes outside the mandate, otherwise produce a signed proof."""
    if q.merchant not in allowlist:
        raise SafeError("merchant not on allowlist")
    if q.amount > cents(cap, "cap"):
        raise SafeError("quote above the per-purchase cap")
    body = asdict(q)
    body.pop("resource")
    body.pop("expires_at")
    return {**body, "signature": _mac(key, body)}
