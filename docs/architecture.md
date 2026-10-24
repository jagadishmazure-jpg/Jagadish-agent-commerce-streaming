# Architecture

```
 browser (index.html + app.js)                    FastAPI app (server.py)
 ┌────────────────────────────┐   POST /api/chat   ┌──────────────────────────────────────┐
 │ form ──────────────────────┼──────────────────► │ CommerceAgent.respond(text)          │
 │                            │  {run_id}          │   -> list of Events (a "run")        │
 │ EventSource ◄──────────────┼── GET /stream ──── │ sse(): sleep(first), then per token  │
 │  token / tool_call /       │   text/event-stream│   checks run.cancelled each event    │
 │  tool_result / guard /     │                    │   scrubs every string field          │
 │  approval_required / done  │                    │                                      │
 │ Approve / Reject ──────────┼── POST /decision ─►│ CommerceAgent.decide(bool)           │
 │ Stop ──────────────────────┼── POST /cancel ───►│ run.cancelled = True                 │
 └────────────────────────────┘                    └──────────────┬───────────────────────┘
                                                                  │
                         ┌────────────────────────────────────────▼──────────────────────┐
                         │ PaymentService (payments.py)                                  │
                         │  vault: card -> tok_...   checkout(mandate, cart, approval,   │
                         │  idempotency map          token, idempotency_key)             │
                         │  used nonces              refund(payment_id, amount, key)     │
                         │  per-mandate spend        FraudPolicy: velocity, spike        │
                         └───────────────────────────────────────────────────────────────┘
   mandate.py: SpendMandate, Cart, CartApproval (HMAC-SHA256 signed)
   x402.py:    PaywalledResource.quote()/redeem(), pay_quote()   (GET /api/paid/report)
   pii.py:     scrub(), ScrubFilter on every logger, SafeError
   personas.py: scripted users + ledger checks -> results/persona_report.json
   bench.py:   uvicorn in a thread + httpx and headless Chromium -> results/stream_bench.json
```

## Modules

| Module | Responsibility |
|---|---|
| `money.py` | Integer cents, validation (no negatives, floats with fractions, NaN, inf, bools; $100,000 ceiling) |
| `mandate.py` | Signed spend mandate, cart and its SHA-256 digest, signed cart approval with nonce and expiry |
| `payments.py` | Vault tokens, checkout checks in a fixed order, fraud policy, refunds, event log |
| `x402.py` | 402 quote, agent-side payment within the mandate, server-side proof redemption |
| `agent.py` | Deterministic agent: intent rules, consent parsing, injection guard, pending action |
| `server.py` | HTTP API, SSE framing, cancel, CSP and other security headers, input validation |
| `static/` | Plain JavaScript client using only `textContent` for output |
| `personas.py` | Persona strategies, ledger-based checks, report |
| `bench.py`, `localserver.py` | Real-server timing over HTTP and in a headless browser |
| `cli.py` | `agentcommerce serve`, `bench`, `personas` |

## Data flow for one purchase

1. "buy a headlamp" → agent searches the catalog and builds `Cart(fernhill-market, [headlamp $29])`.
2. The cart is within the per-purchase cap, so the agent stores it as the *pending* action and emits
   `approval_required` with a human-readable summary.
3. The user presses Approve (or types a clear yes). The user's side signs a `CartApproval` over the
   cart digest and amount with a fresh nonce.
4. `PaymentService.checkout` verifies, in order: idempotency key, mandate signature and expiry,
   approval signature and expiry, nonce not used, approval matches mandate, cart digest and amount
   match, merchant on the allowlist, non-zero amount, per-purchase cap, total cap, known card token,
   fraud policy. Only then is the nonce burned and the receipt written.
5. The reply streams back with the last four digits of the card, never the number.

State is in memory per process. That is fine for a lab and called out in the limitations.
