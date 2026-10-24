# ADR 0001: Signed spend mandate plus per-cart approval

Status: accepted

## Context

The agent needs to buy things without asking for a card each time, but a single "you may spend"
flag is too coarse and a per-purchase card prompt is too slow.

## Decision

Two signed objects. A long-lived mandate (caps, allowlist, expiry) sets the outer limits. A short-lived
approval, bound to one cart digest with a single-use nonce, gives consent for one purchase. The
payment service enforces both and records every refusal.

## Consequences

* The agent can prepare carts freely but cannot capture without a fresh approval.
* Approvals are cheap to create (a button press) and impossible to reuse.
* The lab uses one HMAC key for both; production must move signing to the user's device.
