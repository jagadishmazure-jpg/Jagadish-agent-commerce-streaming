# ADR 0002: Server-sent events for streaming, POST for actions

Status: accepted

## Context

The UI needs tokens and tool events as they happen, plus a way to approve, reject and stop.

## Decision

Each reply is a run with an id. The browser opens `EventSource` on `/api/runs/{id}/stream` and the
server sends typed events (`token`, `tool_call`, `tool_result`, `guard`, `approval_required`, `done`,
`cancelled`). Approve, reject and cancel are separate JSON POSTs. A run can be streamed once.

## Alternatives

WebSockets: two-way, but needs its own auth and framing and is harder to put behind ordinary proxies.
Long polling: simple, but adds latency per token.

## Consequences

Plain JavaScript client with no build step; easy to test with httpx and Playwright; the cancel flag
is checked between events, so cancel latency is about one token interval.
