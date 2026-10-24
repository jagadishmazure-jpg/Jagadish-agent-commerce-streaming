# Purpose

## The problem

Fernhill Market (fictional) wants its support chat to do more than answer questions: buy items for
a customer, issue refunds and pay for small paid reports from a partner, Larkspur Books (also
fictional). The moment an agent can move money, the failure modes change. A wrong answer is
embarrassing; a wrong charge is a chargeback, a support ticket and a lost customer.

This repo is a working lab for the controls that make that safe enough to try, and for the user
interface that keeps the human in charge while the agent works.

## What it shows

1. **Bounded authority.** The user signs a mandate that says how much the agent may spend per
   purchase and in total, at which merchants, until when. The payment service checks the signature
   and every limit on each capture.
2. **Explicit, exact consent.** For each purchase the user approves one specific cart. The approval
   is signed, short-lived, single-use and bound to a hash of the cart, so the agent cannot swap the
   items or the price after the user said yes.
3. **Safe retries.** Checkout and refunds take an idempotency key. Retrying after a timeout returns
   the first receipt instead of charging again; reusing a key for a different request is refused.
4. **A UI that shows the work.** Tokens and tool calls stream live over server-sent events. Risky
   actions stop and wait for Approve or Reject. A Stop button ends a reply mid-stream, and an
   approval prompt that was never displayed can never be acted on.
5. **Testing against difficult people.** Simulated users who are angry, vague, manipulative or keep
   changing their mind are scored on what happened in the ledger.

## Who it is for

Engineers building agent checkout or agent-initiated payments, reviewers who need a concrete
picture of what "human in the loop" means for money, and interviewers looking for evidence of
safety thinking beyond a demo.

## Non-goals

No real processor, no real card data (only the public test number 4242 4242 4242 4242), no real
model. Those are deliberately out of scope so that the whole thing runs free, offline and the same
way every time.
