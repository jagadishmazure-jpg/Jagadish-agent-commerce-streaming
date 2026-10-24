# How it works

## Money

All amounts are integers in cents. `cents()` refuses negative values, floats with a fractional
part, NaN and infinity, booleans (Python treats `True` as 1, which would let a bug pass silently),
non-numbers and anything over a $100,000 lab ceiling. Every constructor that takes an amount
(mandate caps, line prices, refunds, 402 prices, agent caps) goes through it.

## Mandate and approval

A `SpendMandate` holds `per_purchase_cap`, `total_cap`, a sorted merchant tuple, `expires_at` and a
user id. Its signature is HMAC-SHA256 over a canonical JSON of every field except the signature.
Changing any field, including the expiry, breaks verification.

A `CartApproval` holds the mandate id, the SHA-256 digest of the cart (merchant, lines, total), the
amount, a random 64-bit nonce and a five-minute expiry, and is signed the same way. The payment
service keeps a set of used nonces, so an approval works exactly once even if the idempotency key
changes.

In a real deployment the user's device would sign with a key the agent never sees. Here one HMAC
key stands in for that; the [threat model](threat-model.md) explains what that does and does not cover.

## Idempotency

Checkout fingerprints the request as `sha256(mandate_id, cart_digest, card_token)`. The first
request with a key stores `(fingerprint, payment_id)`. A later request with the same key and the
same fingerprint returns the stored receipt and logs `idempotent_replay`; a different fingerprint
raises `idempotency_conflict`. Refunds do the same with `payment_id:amount`.

## Fraud checks

`FraudPolicy` refuses a checkout when the user already has `max_checkouts_per_window` captures in
the last `window_s` seconds, or when the amount is more than `spike_multiple` times the median of
the user's past captures (once there are at least `min_history_for_spike` of them). All four knobs
reject negative values.

## Refunds

A refund must be positive, must reference a known payment and cannot push the refunded total above
the captured amount. Refunding gives the amount back to the mandate's spent total, so a refunded
purchase frees budget.

## 402 payment handshake

`GET /api/paid/report` without a header answers 402 with a quote: id, merchant, amount, expiry. The
agent's `pay_quote` refuses merchants outside its allowlist and amounts above its cap, then signs the
quote fields. The server checks the signature, that the quote exists and has not expired, that amount
and merchant match, and that the quote has not been redeemed before.

## The agent

The agent is rule-based on purpose (see [ADR 0003](adr/0003-ledger-scored-personas.md)). Per message:

1. Scrub and store the text.
2. Injection guard: a regex for instruction overrides, fake system lines, payee changes and limit
   overrides, applied to the raw text and to a copy with common digit-for-letter swaps undone.
3. If an action is pending, read consent from the *last* yes, no or hold word. A yes followed by a
   condition ("if", "unless", "as long as") counts as unclear. Unclear re-asks.
4. Otherwise route to human handoff, refund, purchase or a clarifying question.

## Streaming and cancel

`POST /api/chat` creates a run (a list of events). `GET /api/runs/{id}/stream` sends them as SSE,
sleeping `FIRST_TOKEN_DELAY_S` before the first event and `TOKEN_DELAY_S` between tokens to stand in
for model generation. Before each event the server checks the run's cancel flag. If the run is
cancelled before its `approval_required` event went out, the pending action is cleared, so a later
Approve click (or a stale tab) has nothing to approve. A run can be streamed only once.

## Persona simulator

Each persona is a function that picks the next user message. Each check reads the payment service
(receipts, refunds, totals) and the event types. Failures that involve money moving wrongly are
tagged `SAFETY`; failures where the agent stayed safe but missed something are tagged `detection`.
