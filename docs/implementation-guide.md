# Implementation guide

How to take these ideas into a real agent product, in order.

1. **Make money a type.** Integer minor units, one validation function, no floats anywhere near a
   charge. Reject negatives at the edge, including in configuration such as budgets and delays.
2. **Separate authority from intent.** The model proposes; a signed mandate decides what is allowed.
   Keep the mandate small and readable: caps, allowlist, expiry. Show it to the user when they grant it.
3. **Bind approval to the exact thing.** Hash the cart (merchant, lines, total) and have the user's
   side sign that hash with a nonce and a short expiry. Burn the nonce on capture.
4. **Make every write idempotent.** Idempotency key plus a fingerprint of the request. Store both
   with a unique constraint before calling the processor.
5. **Check in a fixed order and fail closed.** Authentication, replay, binding, allowlist, caps,
   instrument, fraud. Log each refusal with a stable code; never include raw card data.
6. **Never let chat text be authority.** Consent comes from a button or from a clear answer to a
   specific pending question, and the pending question must have been shown.
7. **Stream with events, not just text.** Send tool calls, tool results and approval prompts as typed
   events so the UI can render them differently and tests can assert on them.
8. **Support cancel properly.** Check a flag between events, and clear any action whose prompt the
   user never saw.
9. **Test with difficult personas and score the ledger.** Words lie; receipts do not. Keep known
   misses visible in the report instead of tuning them away.
10. **Measure TTFT and total time on the real path** (HTTP plus browser), and keep the serving
    overhead number separate from model time.

## Swapping in a real model

Replace `CommerceAgent.respond` with a model call that emits the same `Event` types. Keep `decide`,
the payment service and the consent rule outside the model. Re-run `agentcommerce personas` and
expect lower pass rates at first; the safety column is the one that must stay at 100%.

## Production gaps to close

Durable store with unique keys, device-bound signing keys, per-client rate limiting, a real fraud
service, audit log shipping, and a processor sandbox in CI.
