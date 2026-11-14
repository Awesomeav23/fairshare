# ADR-0002: Transactional outbox for all side effects

**Status:** Accepted · **Date:** 2026-11-14

## Context
- A state change (e.g. `PENDING → PROVISIONING`) must lead to a side effect (create Kubernetes resources).
- Writing to Postgres and then publishing to RabbitMQ is a **dual write**. A crash between the two either loses the event, or publishes an event for a change that never committed.

## Decision
- Every state change writes an `outbox` row **in the same transaction**.
- A relay (leader only) publishes unsent rows to RabbitMQ with publisher confirms, then marks them sent.
- Consumers are idempotent (`processed_events` + state checks), because delivery is at-least-once.

## Consequences
- ✅ A committed decision is always acted on eventually. An uncommitted one never is.
- ✅ The scheduler never calls Kubernetes, so it stays fast and testable.
- ❌ Extra latency of up to one relay interval (500 ms) per step.
- ❌ Every consumer must handle duplicates. Covered by tests that deliver each message twice.

## Alternatives considered
| Option | Why not |
|---|---|
| **Publish after commit** | Crash after commit loses the event. The reconciler would catch it, but 30 s later and only for some cases |
| **Call Kubernetes inside the scheduler** | Slow, can't be part of the DB transaction, and a failure leaves half-done decisions |
| **Postgres `LISTEN/NOTIFY` only** | Notifications are lost if nobody is listening. Still needs a table to be reliable, which is the outbox anyway |
