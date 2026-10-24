# Guardrails

| Risk | Control | Where | Test |
|---|---|---|---|
| Overspending | Per-purchase and total caps in a signed mandate | `payments.checkout` | `test_per_purchase_cap`, `test_total_cap` |
| Paying the wrong party | Merchant allowlist in the mandate; agent-side allowlist for 402 quotes | `payments`, `x402.pay_quote` | `test_merchant_not_allowlisted`, `test_x402_agent_refuses_outside_mandate` |
| Forged or edited limits | HMAC over every mandate field | `mandate.verify` | `test_tampered_mandate_fails`, `test_forged_mandate` |
| Stale authority | Mandate and approval expiry | `verify` | `test_expired_mandate`, `test_expired_approval` |
| Cart swapped after approval | Approval bound to the cart digest and amount | `checkout` | `test_cart_swapped_after_approval` |
| Approval replay | Single-use nonce | `checkout` | `test_replay_of_approval_blocked` |
| Double charge on retry | Idempotency key with request fingerprint | `checkout`, `refund` | `test_idempotent_retry_returns_same_receipt`, `test_idempotency_conflict` |
| Bursts or unusual amounts | Velocity and spike checks | `FraudPolicy` | `test_fraud_velocity`, `test_fraud_spike` |
| Over-refund | Refund cannot exceed captured minus refunded | `refund` | `test_refund_partial_full_and_over`, `test_refund_capped_at_captured` |
| Negative or odd amounts | `cents()` everywhere, including budgets and delays | `money`, `server` | `test_negative_*` |
| Prompt injection | Regex guard on raw and de-leeted text; no tool runs on a flagged message | `agent.respond` | `test_injection_detected`, persona suite |
| Ambiguous consent | Last stance wins; conditional yes is unclear | `agent.consent` | `test_consent_reads_final_stance` |
| Acting on an unseen prompt | Cancel before `approval_required` clears the pending action | `server.sse` | `test_cancel_before_stream_clears_unseen_approval` |
| Card numbers leaking | Vault tokens; errors never echo input; scrub filter on loggers; streamed fields scrubbed | `payments`, `pii`, `server` | `test_payment_logs_never_contain_card`, `test_card_in_chat_not_streamed_back` |
| XSS in the chat | `textContent` only; CSP `default-src 'self'`; `nosniff` | `app.js`, `server` | `test_browser_renders_markup_as_text`, `test_security_headers` |
| Oversized or unexpected input | Pydantic models with length limits and `extra="forbid"` | `server` | `test_bad_chat_input` |

## PII checklist

* Cards: only Luhn-valid 13 to 19 digit runs are masked by `scrub`, so order numbers survive. That is
  why `tokenize` never puts the submitted number in its error: a mistyped card fails Luhn and would
  not be masked. A test caught exactly this.
* Emails, phone numbers and SSNs (valid area, group and serial only) are masked.
* Every logger from `get_logger` has `ScrubFilter`, which formats the message first and then scrubs,
  so values passed as `%s` arguments are covered.
* `SafeError` scrubs its message on construction, so exceptions are safe to show and log.
* Stored chat text is scrubbed before it is kept in the session log.
