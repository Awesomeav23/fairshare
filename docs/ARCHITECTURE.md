# Fairshare — Architecture

**Status:** Draft v1 (Phase 0)
**Last updated:** 2026-11-14

Related docs: [PRODUCT_SPEC](PRODUCT_SPEC.md) · [JOB_LIFECYCLE](JOB_LIFECYCLE.md) · [SCHEDULING_POLICY](SCHEDULING_POLICY.md) · [DATA_MODEL](DATA_MODEL.md) · [API](API.md)

---

## 1. Overview

```mermaid
flowchart LR
    subgraph Users
        UI[web<br/>React + TS]
        CLI[curl / scripts]
    end

    subgraph sys[namespace: fairshare-system]
        FS[fairshared<br/>Go]
        PG[(Postgres)]
        MQ[[RabbitMQ]]
        S3[(MinIO)]
    end

    subgraph jobs[namespace: fairshare-jobs]
        subgraph j1[job 42 · attempt 2]
            R[runner pod<br/>Python]
            H1[h2o-0]
            H2[h2o-1]
        end
    end

    UI -->|REST + token| FS
    CLI -->|REST + token| FS
    FS <-->|state, outbox, audit| PG
    FS -->|outbox relay publishes| MQ
    MQ -->|job.* commands| FS
    MQ -->|runner.* events| FS
    FS -->|client-go: create / delete| j1
    FS -->|dataset upload,<br/>result download| S3
    R -->|AutoML via REST| H1
    H1 --- H2
    R -->|runner.* events| MQ
    R <-->|dataset, checkpoints,<br/>results| S3
```

**Core rule:** Postgres is the only source of truth for job state. Kubernetes and RabbitMQ are things Fairshare *acts on*. When they disagree with Postgres, Postgres wins and the reconciler fixes the difference.

---

## 2. Components

| Component | Language | Runs as | Responsibility |
|---|---|---|---|
| **fairshared** | Go | Deployment (1–2 replicas) | API, scheduling, all Kubernetes side effects |
| **runner** | Python | One Pod per job attempt | Drives AutoML on its H2O cluster, saves checkpoints, reports events |
| **H2O-3 cluster** | (H2O-3 image, Java) | One StatefulSet per job attempt | Does the training |
| **web** | React + TS | Static files served by nginx | UI for members, admins, auditors |
| **Postgres** | — | StatefulSet | Tenants, users, datasets, jobs, outbox, audit |
| **RabbitMQ** | — | StatefulSet | Durable event delivery |
| **MinIO** | — | StatefulSet | Datasets, checkpoints, results |

---

## 3. Inside fairshared

One Go binary, split into modules with clear boundaries.

| Module | Runs on | Does | Talks to |
|---|---|---|---|
| `api` | every replica | HTTP routes, validation | `auth`, `store`, `audit` |
| `auth` | every replica | Token → user, role checks | `store` |
| `store` | every replica | All SQL. Only module that touches Postgres | Postgres |
| `audit` | every replica | Appends hash-chained audit rows in the caller's transaction | `store` |
| `scheduler` | leader only | Every 2 s: pick jobs to start or preempt. **Writes to Postgres only** | `store` |
| `relay` | leader only | Publishes outbox rows to RabbitMQ | `store`, RabbitMQ |
| `provisioner` | leader only | Consumes `job.*` commands → Kubernetes calls | RabbitMQ, Kubernetes, `store` |
| `events` | leader only | Consumes `runner.*` events → job state changes | RabbitMQ, `store` |
| `reconciler` | leader only | Every 30 s: compares Postgres with Kubernetes, fixes drift | `store`, Kubernetes |
| `leader` | every replica | Holds a Postgres advisory lock. Starts/stops leader-only modules | Postgres |

### Why split "decide" from "do"
- The **scheduler** decides: in one transaction it changes job state and writes an outbox row.
- The **provisioner** does: it reacts to the event and calls Kubernetes, with retries.
- A crash between the two is safe:
  - Decision committed, event not yet sent → the relay sends it after restart.
  - Event sent, Kubernetes call not finished → the message is redelivered, and the call is idempotent.

### Leader election
- Every replica tries to take Postgres advisory lock `fairshare_leader`, held on a dedicated connection.
- Whoever holds it runs the leader-only modules.
- If the connection drops, the lock is released and the leader stops its loops at once.
- The other replica takes the lock within one retry interval (2 s).
- The API runs on every replica, so the API stays up during a leader handover.

---

## 4. Kubernetes layout

### Namespaces
- `fairshare-system`: fairshared, web, Postgres, RabbitMQ, MinIO.
- `fairshare-jobs`: per-job resources only.

### Per job attempt
Each start or resume is a new **attempt** (1, 2, 3…). Resources are named per attempt, so a new attempt never collides with an old one that's still shutting down.

| Resource | Name | Notes |
|---|---|---|
| StatefulSet | `job-<id>-a<n>-h2o` | `replicas` = job slots. One slot per pod. `podManagementPolicy: Parallel` |
| Headless Service | `job-<id>-a<n>-h2o-discovery` | `publishNotReadyAddresses: true`. Used for H2O discovery **and** by the runner to reach each pod |
| Pod | `job-<id>-a<n>-runner` | `restartPolicy: Never`. Grace period 60 s |

- **No ordinary client Service.** Only the H2O leader is Ready, and non-leaders reject REST calls with 403. While the cloud forms, every pod briefly claims to be leader. So the runner finds the leader itself (§6) instead of trusting a Service ([SPIKE_RESULTS F1, F2](SPIKE_RESULTS.md#4-findings-that-change-the-design)).
- **JVM heap:** `-XX:MaxRAMPercentage=60`. 70% was OOM-killed because H2O uses significant off-heap memory (F5).

- **Labels** on everything: `fairshare.io/job-id`, `fairshare.io/attempt`, `fairshare.io/tenant`.
- **Owner references:** the Service and runner Pod are owned by the StatefulSet. Deleting the StatefulSet cleans up all three.
- **Why a bare Pod, not a Kubernetes Job:** retries are Fairshare's decision, made in Postgres. A Kubernetes Job would retry on its own and break the one-source-of-truth rule.

### H2O cluster formation
- H2O-3 has built-in Kubernetes clustering.
- Each H2O pod gets env vars:
  - `H2O_KUBERNETES_SERVICE_DNS` = the headless Service name
  - `H2O_NODE_EXPECTED_COUNT` = slots
  - `H2O_NODE_LOOKUP_TIMEOUT`
- Pods resolve each other through the headless Service and form one cloud.
- Readiness probe: `GET /kubernetes/isLeaderNode` on port 8080 (`H2O_KUBERNETES_API_PORT`). Only the leader passes.
- **Validated in Spike A:** a 2-node cloud forms in ~7 s with the image preloaded.
- **Once work starts, the cloud is locked.** A replacement pod can't join and crash-loops. A restarted leader comes back as a healthy-looking 1-node cloud with no data. So readiness is **not** a health signal (see §8).

### Safety nets
- **ResourceQuota** on `fairshare-jobs` = cluster capacity. Even a scheduler bug can't overcommit the cluster.
- **RBAC:** fairshared's ServiceAccount can manage StatefulSets, Services and Pods **only** in `fairshare-jobs`.

---

## 5. Messaging (RabbitMQ)

### Topology
- One durable topic exchange: `fairshare.events`.

| Queue | Bound to | Consumer |
|---|---|---|
| `fairshare.provisioner` | `job.provision`, `job.checkpoint`, `job.cancel`, `job.teardown` | `provisioner` |
| `fairshare.runner-events` | `runner.#` | `events` |
| `fairshare.dead-letter` | messages that failed 5 times | none. Shown in admin UI |

### Delivery rules
- **At-least-once.** Duplicates can happen, so every consumer is idempotent.
- Every message has an `event_id` (UUID). Consumers record handled IDs in `processed_events` in the same transaction as their state change. Seen before → acknowledge and skip.
- Consumers also check job state before acting. An event that doesn't fit the current state is acknowledged and logged, not applied.
- The relay uses **publisher confirms**. An outbox row is marked sent only after RabbitMQ confirms it.
- Messages are persistent and queues are durable, so a RabbitMQ restart loses nothing.

### Outbox relay
1. Every 500 ms: select up to 100 unsent outbox rows, oldest first, `FOR UPDATE SKIP LOCKED`.
2. Publish each one and wait for confirms.
3. Set `published_at`.
4. Rows older than 7 days that were sent get deleted.

---

## 6. Runner

One Python process per job attempt. Libraries: `h2o`, `pika` (RabbitMQ), `boto3` (MinIO).

### Normal run
1. **Find the leader:** for each pod `job-<id>-a<n>-h2o-<i>.<discovery service>`, call `:8080/kubernetes/isLeaderNode`. On the pod that answers 200, check `:54321/3/Cloud` until `cloud_size` = expected node count. Connect to that pod. Give up after 4 min.
2. Download the dataset CSV from object storage, then `h2o.upload_file` it into H2O.
3. **If attempt > 1:** load finished models from the job's checkpoint folder (`h2o.upload_model`).
4. Start AutoML with:
   - an **explicit `modeling_plan`**: the full plan on attempt 1; on resume, the full plan minus finished steps ([ADR-0004](adr/0004-per-model-checkpoints.md))
   - `max_models` = requested − already finished
   - `max_runtime_secs` = requested − runtime already used
   - StackedEnsemble excluded, so every saved model is independent. XGBoost isn't available on ARM64 or multi-node clusters (SPIKE_RESULTS F6)
5. A background thread polls the leaderboard every 5 s. For each new model:
   - save the model binary to MinIO
   - send `runner.model_saved`
6. When AutoML ends:
   - build the combined leaderboard (checkpointed + new models)
   - export the best model as a MOJO
   - upload both to MinIO
   - send `runner.finished`

### On SIGTERM (preemption or cancel)
1. Set a **hard deadline of 45 s** (grace period is 60 s).
2. Cancel the running AutoML job through the H2O API.
3. Save any finished models not yet saved, if the deadline allows.
4. Write the manifest from what is **already in object storage**, even if H2O is unreachable.
5. Send `runner.checkpointed` with the saved models and `h2o_reachable`.
6. Exit 0 with `os._exit`, because H2O client threads can be stuck retrying forever (SPIKE_RESULTS F4).

Measured: 0.3 s normally, 0.7 s with H2O already dead.

### On error
- Send `runner.failed` with the reason, then exit non-zero.
- If the runner dies without sending anything (OOM, node loss), the reconciler sees the failed Pod.

### Spike B answers
- Models saved from one cluster load into a new one: **yes**.
- AutoML can skip finished steps via `modeling_plan`: **yes**.
- `h2o.make_leaderboard` combines loaded + new models: **yes**.

---

## 7. Object storage layout (MinIO)

| Bucket | Path | Written by |
|---|---|---|
| `datasets` | `<tenant_id>/<dataset_id>.csv` | fairshared (upload) |
| `checkpoints` | `<job_id>/models/<model_id>.bin` | runner |
| `checkpoints` | `<job_id>/manifest.json` | runner (finished models + runtime used) |
| `results` | `<job_id>/leaderboard.json` | runner |
| `results` | `<job_id>/best_model.zip` (MOJO) | runner |

- Checkpoints are deleted 7 days after a job reaches a final state.
- Users never access MinIO directly. fairshared streams uploads and downloads after checking permissions.
- **Credentials:** fairshared and runners use separate MinIO users.
  - Runner user: read `datasets`, read/write `checkpoints` and `results`. No delete.
  - Not in v1: per-job credentials scoped to one job's paths.

---

## 8. Reconciler

Runs every 30 s on the leader. For each non-final job, compare the expected and actual Kubernetes state.

| Postgres says | Kubernetes shows | Action |
|---|---|---|
| `PROVISIONING` for > 2 min | no resources | Re-emit `job.provision` |
| `RUNNING` | runner Pod `Failed` or missing | Attempt failed → [JOB_LIFECYCLE](JOB_LIFECYCLE.md) retry rules |
| `PROVISIONING` / `RUNNING` | any H2O pod has `restartCount > 0` or is in `CrashLoopBackOff` | H2O cloud is broken → delete attempt → attempt failed. **Readiness is ignored**: a restarted leader looks Ready |
| `CHECKPOINTING` for > 90 s | runner gone, no `checkpointed` event | Use the last manifest in MinIO → `PREEMPTED` |
| final state | resources still exist | Delete them (orphans) |
| *(no job)* | resources with Fairshare labels | Delete them |

- Every action is audited with actor `system:reconciler`.
- Exact transitions live in [JOB_LIFECYCLE](JOB_LIFECYCLE.md).

---

## 9. Security

- **Tokens:** random 32 bytes, shown once, stored as SHA-256.
- **Secrets:** DB, RabbitMQ and MinIO credentials live in Kubernetes Secrets, never in images or Git.
- **Least privilege:**
  - fairshared's Kubernetes rights are limited to `fairshare-jobs` (§4).
  - Runners have no Kubernetes API access (`automountServiceAccountToken: false`).
  - Separate RabbitMQ users: runners may only publish `runner.*`.
- **Containers:** non-root, read-only root filesystem where possible.
- **Not in v1:** NetworkPolicy. kind's default network plugin doesn't enforce it.

---

## 10. Local development

Two modes.

### Dev mode (Phases 1–7)
- Postgres, RabbitMQ and MinIO run in Docker Compose.
- fairshared runs on the host and uses your kubeconfig to reach kind.
- Vite dev server for the UI.

| Service | Port |
|---|---|
| Postgres | 5438 |
| fairshared API | 8030 |
| Vite | 5200 |
| RabbitMQ (AMQP / UI) | 5672 / 15672 |
| MinIO (API / console) | 9000 / 9001 |

- Runner pods inside kind reach host services through `host.docker.internal`.

### In-cluster mode (Phase 8+)
- Everything is installed by the Helm chart into kind.
- This is what CI and the one-command install use.

### Prerequisites to install
- Docker Desktop (running), Go, Helm. kind, kubectl, Python and Java are already present.

---

## 11. Repo layout

```
fairshare/
├── controller/           # Go: fairshared
│   ├── cmd/fairshared/
│   ├── internal/         # api, auth, store, audit, scheduler, relay,
│   │                     # provisioner, events, reconciler, leader
│   └── migrations/       # SQL migrations
├── runner/               # Python runner (+ fake runner mode)
├── web/                  # React + TS (Vite)
├── deploy/
│   ├── compose/          # dev-mode dependencies
│   ├── helm/fairshare/   # Helm chart
│   └── kind/             # kind cluster config
├── spikes/               # Phase 0 experiments (not production code)
├── docs/
└── Makefile
```

---

## 12. Libraries

All are libraries for JD-stack languages.

| Area | Choice |
|---|---|
| Go HTTP router | `chi` |
| Go Postgres driver | `pgx` |
| Go migrations | `golang-migrate` |
| Go Kubernetes | `client-go` |
| Go RabbitMQ | `amqp091-go` |
| Go MinIO | `minio-go` |
| Python H2O | `h2o` |
| Python RabbitMQ | `pika` |
| Python MinIO | `boto3` |
| Web | React, TypeScript, Vite, TanStack Query |
