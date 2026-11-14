# Fairshare

A multi-tenant control plane for running H2O-3 AutoML training jobs on a shared Kubernetes cluster.

Several teams share one cluster. Fairshare decides whose job runs next, enforces per-team quotas, preempts low-priority work when urgent jobs arrive, and resumes preempted jobs from saved progress instead of starting over. Job state survives controller crashes, and every action lands in a tamper-evident audit log.

## Status

**Phase 0 complete:** design docs written, risky assumptions tested in spikes. Next: Phase 1 (skeleton).

## Design docs

| Doc | Covers |
|---|---|
| [Product spec](docs/PRODUCT_SPEC.md) | Users, scope, requirements, success criteria |
| [Architecture](docs/ARCHITECTURE.md) | Components, Kubernetes layout, messaging, runner |
| [Job lifecycle](docs/JOB_LIFECYCLE.md) | States, transitions, retries, crash behavior |
| [Scheduling policy](docs/SCHEDULING_POLICY.md) | Fair share, reservations, preemption |
| [Data model](docs/DATA_MODEL.md) | Postgres tables, constraints, audit hash chain |
| [Spike results](docs/SPIKE_RESULTS.md) | Phase 0 experiments: what was proven, what changed |
| [API](docs/API.md) | REST endpoints, auth, errors |
| [ADRs](docs/adr/) | Key decisions and the alternatives rejected |

## Planned stack

| Area | Tech |
|---|---|
| Controller + API | Go |
| Training runners | Python, H2O-3 AutoML |
| UI | React + TypeScript |
| Data | PostgreSQL, SeaweedFS (S3-compatible object storage), RabbitMQ |
| Platform | Docker, Kubernetes (kind), Helm |
| CI/CD | GitHub Actions, Jenkins |

## Roadmap

| Phase | Scope |
|---|---|
| 0 | Spikes (H2O-3 on kind, checkpoint/resume, runner pod) + design docs |
| 1 | Skeleton: repo layout, Go API, Postgres migrations, Compose, CI |
| 2 | One AutoML job end to end on Kubernetes |
| 3 | Crash-safe job state machine (outbox → RabbitMQ, reconciler) |
| 4 | Tenants, quotas, weighted fair-share scheduling |
| 5 | Preemption + checkpoint/resume |
| 6 | React UI |
| 7 | Tamper-evident audit log + RBAC |
| 8 | Helm chart, multi-arch images, Jenkins + GitHub Actions, kind e2e in CI |
| 9 | Failure testing, load testing, docs |
