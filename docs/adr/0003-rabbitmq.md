# ADR-0003: RabbitMQ as the message queue

**Status:** Accepted · **Date:** 2026-11-14

## Context
- Runners need to report events even while fairshared is down or restarting.
- The outbox needs somewhere durable to publish to.
- Tech must come from the target stack, which lists "message queues" without naming one.

## Decision
- **RabbitMQ**: one durable topic exchange, durable queues, persistent messages, publisher confirms, manual acks, dead-letter queue after 5 failed deliveries.

## Consequences
- ✅ Runner events buffer in RabbitMQ when fairshared is down. Nothing is lost.
- ✅ Per-message acks and dead-lettering fit command-style messages well.
- ✅ Management UI on 15672 helps debugging.
- ❌ One more stateful service to run.
- ❌ No replay of old messages (unlike a log). Not needed: Postgres holds history.

## Alternatives considered
| Option | Why not |
|---|---|
| **Kafka** | Built for high-volume streams and replay. Heavier to run on a 16 GB laptop. Per-message retry/dead-letter must be built by hand |
| **Runners call fairshared over HTTP** | Events are lost or need client-side retry queues whenever fairshared is down |
| **Postgres as the queue** (`SKIP LOCKED` table) | Works, but runners would need DB credentials. RabbitMQ gives them a narrow publish-only permission instead |
