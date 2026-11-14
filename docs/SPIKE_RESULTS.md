# Fairshare — Phase 0 Spike Results

**Date:** 2026-11-14
**Environment:** MacBook (Apple Silicon, 16 GB), Docker Desktop 29.7.2 with **7.6 GB** memory, kind v0.33.0 (Kubernetes v1.37.0), H2O-3 **3.46.0.12**, Python 3.13.
**Code:** [`spikes/`](../spikes/) (throwaway, not production code).

---

## Summary

| Spike | Question | Result |
|---|---|---|
| **A** | Does a multi-pod H2O cluster form on kind? What happens when a pod dies? | ✅ Forms in **~7 s**. Losing a pod breaks the cluster permanently: confirms ADR-0005 |
| **B** | Can finished models be saved, loaded into a *new* cluster, and AutoML continue without redoing work? | ✅ All three checks pass, including skipping finished steps with `modeling_plan` |
| **C** | Can a runner pod drive AutoML in-cluster and checkpoint on SIGTERM inside the grace period? | ✅ `checkpointed` **0.3 s** after SIGTERM; **0.7 s** even when H2O is already dead |

Both pending ADRs are now **validated**. Nine findings change the design. Three need a decision (§5).

---

## 1. Spike A: H2O cluster on kind

### What was run
- Custom ARM64 image: `eclipse-temurin:17-jre` + `h2o.jar` copied from the pip `h2o` package (client and server versions always match).
- StatefulSet with 2 pods, `podManagementPolicy: Parallel`, headless discovery Service.
- Env: `H2O_KUBERNETES_SERVICE_DNS`, `H2O_NODE_EXPECTED_COUNT`, `H2O_NODE_LOOKUP_TIMEOUT` (names confirmed inside the jar, `water/k8s/*`).

### Results
| Measure | Value |
|---|---|
| Pods created → 2-node cloud formed | **7 s** (19:31:27 → 19:31:34), image preloaded |
| Heap per node at 1 GiB limit, 70% heap | 694 MB |
| Readiness | Only the **leader** passes `/kubernetes/isLeaderNode` on port 8080 |

### Node loss (cloud locked by uploading data, then one pod deleted)
- The StatefulSet recreated the pod. The new pod **crash-looped**:
  `Attempting to join an H2O cloud that is no longer accepting new H2O nodes … FATAL Exiting.`
- The client call that was in progress **hung for minutes** instead of failing.
- Separately, in Spike C, the **leader** was OOM-killed. It restarted as a **healthy-looking 1-node cloud** (Ready = true) that had lost all jobs and data.

**Conclusion:** a lost pod means the attempt is dead. Readiness can't be trusted to show it. Restart count can.

---

## 2. Spike B: checkpoint and resume

### What was run
1. Cluster X (1 node): upload 60,000-row dataset, start AutoML (`max_models=8`, no StackedEnsemble).
2. Poll every 1 s; save each new model (`h2o.download_model`) to S3.
3. After 3 models, cancel the AutoML job (simulated preemption).
4. Delete cluster X entirely. Create cluster Y.
5. Load the 3 models from S3 (`h2o.upload_model`), run AutoML for the 5 remaining with a `modeling_plan` that skips the finished steps.
6. Build a combined leaderboard (`h2o.make_leaderboard`).

### Results
| Check | Result |
|---|---|
| Cancel → training thread stopped | **1.2 s** |
| Models load into a brand-new cluster | ✅ All 3 scored on validation data (AUC 0.713 / 0.702 / 0.677) |
| Resume skips finished work | ✅ Attempt 2 ran **exactly** the 5 remaining steps (GBM def_2/3/4/1, DRF XRT). 3 + 5 = 8 = `max_models` |
| Combined leaderboard | ✅ 8 models from both attempts ranked together |
| `modeling_steps` after cancel | Lists **only the steps that ran**, in order |

### Model sizes (60k rows)
| Model | Size |
|---|---|
| GLM | 0.2 MB |
| GBM | 0.6 MB |
| DRF / XRT | **~19 MB** |

---

## 3. Spike C: runner pod in the cluster

### What was run
- Runner image: `python:3.13-slim` + `h2o` + `boto3`, with the server jar deleted → **189 MB**.
- 2-node H2O cluster (1.5 GiB each) + runner Pod (`restartPolicy: Never`, grace 60 s, no service account token).
- After 3 saved models, `kubectl delete pod` (exactly what preemption does).

### Results
| Measure | Value |
|---|---|
| Runner start → connected to leader of full cloud | 6.5–6.7 s |
| S3 from a pod via `host.docker.internal` | ✅ Works (dev mode assumption holds) |
| SIGTERM → `checkpointed` event | **0.3 s** |
| Pod deleted → gone | 1 s |
| SIGTERM after H2O cluster already killed | `checkpointed` with `h2o_reachable: false` in **0.7 s** |

### Failures found along the way (all fixed in the spike)
1. **403 from non-leader.** During cloud formation, **every pod briefly reports itself as leader**, so the Service routed to both. Fix: runner checks each pod directly by StatefulSet DNS and connects to the leader only once `cloud_size == expected`.
2. **OOMKilled at 1 GiB** with 70% heap. Fix: heap 60%, and 1.5 GiB worked with 60k rows.
3. **SIGTERM path hung** when H2O died. The H2O client retries job polling forever, and the pod was SIGKILLed at 60 s without checkpointing. Fix: hard 45 s deadline, manifest written from what's already in S3, `os._exit` at the end.

---

## 4. Findings that change the design

| # | Finding | Change | Where |
|---|---|---|---|
| F1 | Only the leader is Ready; non-leaders reject REST calls (403) | Discovery Service needs `publishNotReadyAddresses: true`. **No client Service**: runner connects to the leader pod directly | ARCHITECTURE §4, §6 (updated) |
| F2 | Every pod claims leader while the cloud forms | Runner waits for `cloud_size == expected` on the leader before connecting | ARCHITECTURE §6 (updated) |
| F3 | Lost pod → replacement crash-loops; restarted leader looks healthy | Reconciler: **any H2O pod with restartCount > 0, or CrashLoopBackOff** → attempt failed. Never trust readiness | ARCHITECTURE §8 (updated) |
| F4 | H2O client calls can hang forever when a node dies | Runner: hard deadline on SIGTERM path; reconciler (not the runner) detects dead clusters | ARCHITECTURE §6 (updated) |
| F5 | 70% heap OOMs; off-heap use is significant | `-XX:MaxRAMPercentage=60` | ARCHITECTURE §4 (updated) |
| F6 | XGBoost unavailable on ARM64 **and** in multi-node clusters | AutoML always runs without XGBoost. Accepted | PRODUCT_SPEC §6 non-goals (updated) |
| F7 | Resume needs the plan order to know which steps are left | Runner passes an **explicit `modeling_plan` on every attempt**; resume = plan − finished steps. nth saved model of a family ↔ nth executed step of that family | ADR-0004 (updated) |
| F8 | H2O startup is ~7 s, not 30–60 s | Resume is cheap. Provisioning timeout (5 min) is generous; keep it for image pulls | ADR-0005 (updated) |
| F9 | DRF/XRT models are ~19 MB each | Storage per job up to ~100–150 MB. 7-day checkpoint retention is fine | — |

---

## 5. Decisions needed

### D1. Object storage: MinIO is no longer available
- `minio/minio` no longer exists on Docker Hub; `quay.io/minio/minio` requires authorization; Bitnami's image is gone.
- **Spike used SeaweedFS** (`chrislusf/seaweedfs`, Apache-2.0, ARM64, single container `server -s3`). boto3 worked unchanged.
- **Recommendation:** replace MinIO with **SeaweedFS** everywhere (dev Compose, Helm chart, docs). The code talks plain S3 API, so this is a config change, and AWS S3 still works later.
- Go client: `minio-go` works against any S3 API, or use `aws-sdk-go-v2`.

### D2. Slot memory and local capacity
- The design assumed 4 slots × 2 GiB = 8 GiB. **Docker Desktop has 7.6 GiB in total**, and the `canary` kind cluster already uses ~2–2.8 GiB.
- Spike evidence: 1.5 GiB per H2O pod with 60% heap trained on 60k rows fine.

| Option | Slot | Capacity | Needs |
|---|---|---|---|
| **A (recommended)** | 1 CPU + 1.5 GiB | 4 (6 GiB) | Raise Docker Desktop memory to **10 GB**, and stop the `canary` cluster while working on Fairshare |
| B | 1 CPU + 1.5 GiB | 2 (3 GiB) | Nothing. But `large` (4 slots) can't run locally, so demo scenario 1 must use `medium` |
| C | 1 CPU + 2 GiB | 4 (8 GiB) | Docker Desktop at **12 GB+**; tight on a 16 GB Mac |

### D3. Dataset path into H2O
- Spike: runner downloads the CSV from S3, then `h2o.upload_file` (client → server). Works well up to the 100 MB limit.
- Alternative: H2O reads S3 directly (`import_file("s3://…")`), which needs H2O's S3 settings pointed at a custom endpoint.
- **Recommendation:** keep the download-then-upload approach for v1. Simpler, and it's already proven.

---

## 6. Reproducing

See [`spikes/README.md`](../spikes/README.md).
