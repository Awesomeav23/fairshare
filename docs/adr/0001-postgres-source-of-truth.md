# ADR-0001: Postgres is the source of truth; the controller drives Kubernetes

**Status:** Accepted · **Date:** 2026-11-14

## Context
- Job state has to survive crashes of the controller, RabbitMQ and Postgres itself (NFR-1).
- Quotas, users, datasets and the audit log need a relational database regardless.
- Job state could live in Postgres, in Kubernetes (custom resources), or in a queue.

## Decision
- Job state lives **only in Postgres**.
- fairshared creates and deletes plain Kubernetes objects (StatefulSet, Service, Pod) with client-go.
- A reconciler compares Postgres with Kubernetes every 30 s. Postgres wins.

## Consequences
- ✅ One source of truth. Crash recovery = "read Postgres, make Kubernetes match."
- ✅ The scheduler is plain Go over a snapshot, so it's easy to unit test and simulate.
- ✅ State changes, outbox rows and audit rows commit in one transaction.
- ❌ Not Kubernetes-native: no `kubectl get automljobs`.
- ❌ We write our own reconciler instead of using controller-runtime's.

## Alternatives considered
| Option | Why not |
|---|---|
| **Operator with an `AutoMLJob` custom resource** | State in etcd **and** Postgres (quotas, audit) → two sources of truth that can disagree. Cross-store transactions are impossible |
| **Runners pull jobs from a queue** | The queue becomes the scheduler. Weighted fair share, reservations and preemption don't fit a FIFO queue |
