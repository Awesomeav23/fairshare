# Architecture Decision Records

Short records of major design decisions: what was decided, why, and what it costs.

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgres-source-of-truth.md) | Postgres is the source of truth; the controller drives Kubernetes | Accepted |
| [0002](0002-transactional-outbox.md) | Transactional outbox for all side effects | Accepted |
| [0003](0003-rabbitmq.md) | RabbitMQ as the message queue | Accepted |
| [0004](0004-per-model-checkpoints.md) | Checkpoint per finished model | Accepted, validated (Spike B) |
| [0005](0005-preempt-whole-cluster.md) | Preemption deletes the whole H2O cluster; resources are per attempt | Accepted, validated (Spike A) |

New ADRs: copy the format of an existing one, take the next number, add a row here. Superseded ADRs stay, with status changed to "Superseded by NNNN".
