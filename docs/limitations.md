# Limitations

Stated plainly so nobody over-reads the numbers.

1. **The model is simulated.** The agent is deterministic rules. TTFT and total time measure the
   serving path around a fixed simulated delay, not a real model's speed. Swapping in a real model
   changes the agent's decisions too, and the persona pass rates would need re-measuring.
2. **The injection guard is a regex.** It misses paraphrases (17 of 40 injection conversations), and
   a determined attacker will find more. Safety does not rest on it: money only moves after a clear
   yes on a pending approval that the user saw. With a real model the guard should be one layer
   among several (separate tool permissions, the approval step, output checks).
3. **One shared HMAC key.** The same key signs mandates and approvals and is held by the server. A
   real system would sign on the user's device with a key the agent and the merchant never hold.
4. **In-memory state.** Sessions, runs, receipts, nonces and idempotency keys live in process memory.
   Restarting forgets them, and two workers would not share them. Production needs a durable store
   with unique constraints on nonce and idempotency key.
5. **Chat "yes" is executed when the message is posted.** The agent builds the event list at
   `POST /api/chat` time, so a typed yes captures payment before streaming starts; Stop only stops
   the display. The Approve button has the same property. The cancel protection covers prompts the
   user never saw, not payments already approved.
6. **Fraud rules are toy rules.** Velocity and median spike only; no device, location or network
   signals.
7. **Consent parsing is English keyword matching.** It does not understand sarcasm, negation scope
   ("I don't not want it") or other languages.
8. **Latency is machine-specific.** The recorded numbers are from one development machine; CI checks
   relations, not values.
