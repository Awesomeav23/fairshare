# Fairshare — Product Spec

**Status:** Draft v1 (Phase 0)
**Last updated:** 2026-11-14

Related docs: [ARCHITECTURE](ARCHITECTURE.md) · [JOB_LIFECYCLE](JOB_LIFECYCLE.md) · [SCHEDULING_POLICY](SCHEDULING_POLICY.md) · [DATA_MODEL](DATA_MODEL.md) · [API](API.md) · [ADRs](adr/)

---

## 1. Summary

Fairshare lets several teams share one Kubernetes cluster for H2O-3 AutoML training.

- Teams submit training jobs.
- Fairshare decides **whose job runs next**, based on each team's share of the cluster.
- **Urgent jobs can pause** lower-priority jobs.
- Paused jobs **resume from saved progress**, not from zero.
- **No job is lost or run twice**, even if Fairshare itself crashes.
- Every action is recorded in a **tamper-evident audit log**.

---

## 2. Problem

One cluster, limited CPU and memory, several teams.

Without a control plane:
- **Hogging:** one team submits many large jobs and blocks everyone else.
- **No priorities:** an urgent job waits behind routine work.
- **Lost work:** a crash or restart throws away hours of training.
- **No accountability:** nobody can prove who ran what, or when.

---

## 3. Users and roles

Every user has exactly one role.

| Role | Belongs to a tenant? | Can do |
|---|---|---|
| **admin** | No | Manage tenants, quotas, users, tokens. See all jobs. Cancel any job. |
| **tenant-member** | Yes, exactly one | Upload datasets, submit/cancel jobs, see results — **own tenant only** |
| **auditor** | No | Read-only view of all tenants, jobs and the audit log. Run audit verification. |

A **tenant** is a team (e.g. fraud, marketing, risk).

---

## 4. Core concepts

| Concept | Meaning |
|---|---|
| **Slot** | Unit of cluster capacity. 1 slot = 1 CPU + 2 GB memory. |
| **Cluster capacity** | Total slots Fairshare may use. Configurable. Local default: **4**. |
| **Job size** | `small` = 1 slot, `medium` = 2, `large` = 4. One H2O pod per slot. |
| **Weight** | A tenant's fair share. Share = tenant weight ÷ sum of weights of tenants with work. |
| **Max slots** | Hard cap on a tenant's slots in use. Default = cluster capacity. |
| **Priority** | `low`, `normal`, `urgent`. |
| **Borrowing** | A tenant may use more than its share while capacity is idle. Borrowed slots are the first to be preempted. |
| **Preemption** | Pausing a running job to free its slots for a higher-priority job. |
| **Checkpoint** | The models a job has finished so far, saved to object storage. |
| **Dataset** | An uploaded CSV, owned by one tenant. |

---

## 5. Use cases

### Tenant member
- **UC-1** Upload a CSV and get a dataset ID.
- **UC-2** Submit an AutoML job: dataset, target column, size, priority, limits.
- **UC-3** See the job queue, including **why a job is waiting**.
- **UC-4** Watch a job's state timeline: scheduled, running, preempted, resumed, finished.
- **UC-5** See the leaderboard and download the best model as a MOJO.
- **UC-6** Cancel a pending or running job.

### Admin
- **UC-7** Create tenants and set weight and max slots.
- **UC-8** Create users, assign roles and tenants, issue and revoke tokens.
- **UC-9** See cluster usage by tenant.

### Auditor
- **UC-10** Browse the audit log, filtered by tenant, user, job or time.
- **UC-11** Verify the audit log and get a pass, or the first tampered entry.

---

## 6. Scope

### In scope (v1)
- Datasets: CSV upload, stored in MinIO, tenant-isolated.
- Jobs: H2O-3 AutoML on a per-job H2O cluster on Kubernetes.
- Weighted fair-share scheduling on **current** slot usage.
- Priorities, preemption, per-model checkpoint and resume.
- Crash-safe job state (Postgres source of truth, outbox → RabbitMQ, reconciler).
- API-token auth with three roles.
- Tamper-evident audit log with verification.
- React UI for all three roles.
- Helm chart, multi-arch images (x86 + ARM), GitHub Actions + Jenkins.
- Runs on a local **kind** cluster.
- **Fake runner mode** for scale and failure tests without real H2O (simulates models finishing on a timer).

### Non-goals (v1)
- **Cloud deployment** (EKS/GKE/AKS). Possible later; Helm + S3-compatible storage keep the path open.
- **Usage-history fairness** (decayed slot-hours). Phase 9 stretch goal.
- **Resuming a model mid-training.** Resume is per finished model only.
- **Scoring/serving** trained models. Fairshare trains; it does not serve.
- **Data sources other than CSV upload** (databases, Parquet, URLs).
- **Password login, SSO, OAuth.** Tokens only.
- **Multiple Kubernetes clusters** or node-aware placement.
- **GPUs.**
- **Driverless AI or Sparkling Water** support.
- **Email/Slack notifications.**

---

## 7. Functional requirements

### Datasets
- **FR-1** Upload CSV up to **100 MB**. The first row must be a header.
- **FR-2** On upload, record the file name, size, row count and column names.
- **FR-3** Members see and use only their own tenant's datasets. Admins and auditors see all.
- **FR-4** A dataset cannot be deleted while a job that uses it is not finished.

### Jobs
- **FR-5** Job inputs:
  - dataset ID, target column (must exist in the dataset)
  - size: `small` / `medium` / `large`
  - priority: `low` / `normal` / `urgent`
  - `max_models`: 1–50, default 10
  - `max_runtime_secs`: 60–7200, default 900 (counts running time only, not queue time)
  - `seed`: optional
- **FR-6** Problem type (classification or regression) is detected by H2O from the target column.
- **FR-7** A job's size cannot exceed its tenant's max slots or the cluster capacity. Otherwise it is rejected on submit.
- **FR-8** Each tenant may have at most **1 urgent job** pending or running at a time.
- **FR-9** States and transitions follow [JOB_LIFECYCLE](JOB_LIFECYCLE.md). Illegal transitions are rejected.
- **FR-10** Each waiting job shows a reason, e.g. "waiting for 2 free slots" or "tenant at max slots".
- **FR-11** On success, store the leaderboard and the best model (MOJO) in MinIO.
- **FR-12** Cancelling a running job stops it within **60 s** and frees its slots.

### Scheduling and preemption
- **FR-13** When slots are free, the next job comes from the tenant **furthest below its fair share**. Exact rules: [SCHEDULING_POLICY](SCHEDULING_POLICY.md).
- **FR-14** Only a job with **strictly higher** priority may preempt another job.
- **FR-15** Victims are chosen from borrowed slots first, then lowest priority, then most recently started.
- **FR-16** A job can be preempted at most **2 times**. After that it cannot be preempted.
- **FR-17** A preempted job returns to the queue keeping its original submit time.
- **FR-18** A resumed job skips models that already finished. At most the one in-progress model is lost.

### Auth and roles
- **FR-19** Every API call except health checks needs a valid token.
- **FR-20** Tokens are shown once when created. Only a hash is stored.
- **FR-21** Revoked tokens stop working immediately.
- **FR-22** Role and tenant rules from §3 are enforced on every endpoint.

### Audit
- **FR-23** Every change is logged: who, what, when, target and before/after where relevant. This covers all API writes and all job state changes.
- **FR-24** Each audit entry stores the hash of the entry before it (hash chain).
- **FR-25** Verification reports **pass**, or the ID of the first broken entry.
- **FR-26** No one can edit or delete audit entries through the app.

---

## 8. Non-functional requirements

| ID | Requirement |
|---|---|
| **NFR-1** | **No lost or duplicated jobs** when the controller, RabbitMQ or Postgres is killed at any point. |
| **NFR-2** | When slots free up, the next job is scheduled within **5 s**. |
| **NFR-3** | A preempted job's checkpoint finishes within the **60 s** shutdown grace period. |
| **NFR-4** | A dead H2O pod or runner is detected and the job is handled within **60 s**. |
| **NFR-5** | Fits on a 16 GB laptop: 4 slots (8 GB) plus support services. |
| **NFR-6** | All container images run as non-root and are built for x86 and ARM. |
| **NFR-7** | Only the JD stack: Go, Python, Java, React + TS, H2O-3, Kubernetes, Docker, Helm, Postgres, MinIO, RabbitMQ, GitHub Actions, Jenkins — plus libraries for those languages. |
| **NFR-8** | One command takes a clean machine to a running system on kind. |

---

## 9. Success criteria (demo scenarios)

Fairshare v1 is done when all five pass.

1. **Preemption walkthrough**
   - Marketing runs a `large` `normal` job.
   - Fraud submits a `medium` `urgent` job → marketing is checkpointed and paused.
   - Fraud's job finishes → marketing resumes, skipping its finished models.
2. **Crash test**
   - Kill `fairshared` at each lifecycle stage (scheduled, provisioning, running, checkpointing).
   - After restart: every job reaches a correct final state. None lost, none run twice.
3. **Fairness simulation** (fake runner)
   - 3 tenants, weights 4/3/3, all with a full backlog, 30 minutes.
   - Each tenant's average slot usage is within **±10%** of its share.
4. **Tamper test**
   - Edit one audit row directly in Postgres → verification names that row.
5. **Fresh install**
   - Clean machine → one command → kind + Helm install → scenario 1 runs.

---

## 10. Constraints

- **Local only:** kind on a 16 GB Mac (10 CPUs).
- **Tech:** NFR-7 list only. No Prometheus, Grafana, Trivy, etc.
- **Assumptions confirmed by Phase 0 spikes:**
  - Multi-pod H2O-3 clusters form on kind via a headless Service.
  - Finished AutoML models can be saved to MinIO and loaded into a new H2O cluster.
  - If a spike fails, this spec changes before Phase 1.

---

## 11. Decisions

| ID | Decision | Why |
|---|---|---|
| D-01 | Quotas in **slots** | Simple fairness math, easy to show in the UI |
| D-02 | **CSV upload** to MinIO | Realistic, tenant-isolated |
| D-03 | **API tokens** for auth | Keeps the effort on the scheduler |
| D-04 | Fairness on **current** usage | Easy to reason about and test |
| D-05 | **Postgres is the source of truth**; controller drives Kubernetes | One source of truth for crash-safety ([ADR-0001](adr/0001-postgres-source-of-truth.md)) |
| D-06 | **Local kind** only | Cloud deferred to keep scope manageable |
| D-07 | Checkpoint **per finished model** | H2O AutoML can't pause mid-run |
| D-08 | Preemption deletes the **whole H2O cluster** | H2O-3 clusters can't shrink |
