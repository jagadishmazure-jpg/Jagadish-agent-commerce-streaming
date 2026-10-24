# ADR 0003: Deterministic agent, personas scored from the ledger

Status: accepted

## Context

Testing an agent that moves money against difficult users needs repeatable runs and a definition of
"pass" that cannot be fooled by a polite reply.

## Decision

Use a deterministic rule-based agent and seeded persona scripts so every run is identical and free.
Score each conversation from the payment service state (captures, refunds) and event types, and tag
money-moving failures as SAFETY separately from detection misses.

## Consequences

* The report is reproducible and CI checks it.
* Known misses (paraphrased injection) stay in the report and are pinned by a test.
* Pass rates say nothing about a real model; they must be re-measured after swapping one in.
