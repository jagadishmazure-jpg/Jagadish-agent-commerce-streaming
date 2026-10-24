# Interview points

Short answers I can back with code and numbers from this repo.

**How do you stop an agent from overspending?** The agent never holds authority. The user signs a
mandate with per-purchase and total caps, a merchant allowlist and an expiry, and the payment service
checks the signature and limits on every capture. Tests cover forged fields, expired mandates, both
caps and off-list merchants.

**How do you know the user approved this exact purchase?** The approval signs the cart's SHA-256
digest and amount with a single-use nonce. Swapping the cart after approval fails with
`cart_changed`; reusing the approval fails with `replay`.

**What happens when the client retries a checkout?** Same idempotency key and same request returns
the first receipt; same key with a different request is refused. Refunds work the same way.

**What did testing with personas find?** Two real bugs: a conditional "yes if it is under $10" was
charged (3 wrong charges in 40 conversations), and leetspeak slipped past the injection guard. After
fixes: 0 wrong charges in 200 conversations. One gap remains visible on purpose: paraphrased
injection is detected in only 23 of 40 runs, yet no money moves because consent is separate.

**How fast is the streaming?** On this machine the serving path adds about 2 to 4 ms to TTFT on top
of the simulated model's 120 ms; headless Chromium sees the first token about 123 ms after opening
the stream, and Stop takes effect in about 21 ms, one token interval.

**Why SSE instead of WebSockets?** Traffic is one-way during a reply, SSE works through ordinary HTTP
infrastructure, the browser reconnects for free, and user actions are plain POSTs that are easy to
validate and log. See ADR 0002.

**What would you change for production?** Device-bound signing keys, a durable store with unique
constraints on nonces and idempotency keys, a real fraud service, and a real model behind the same
event interface, with the persona suite as a release gate.

**Numbers in this repo:** <!-- BEGIN:tests -->170<!-- END:tests --> tests; persona safety 100% across 5 personas x 40 runs.
