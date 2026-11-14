# ADR-0005: Preemption deletes the whole H2O cluster; resources are per attempt

**Status:** Accepted, validated by Spike A · **Date:** 2026-11-14

## Context
- An H2O-3 cluster is a fixed set of nodes. If any node leaves, the cluster is unusable.
- So a running job can't give back *some* of its slots.
- A preempted or retried job must start again on fresh resources while the old ones may still be shutting down (60 s grace period).

## Decision
- Preemption checkpoints, then deletes the job's **entire** H2O cluster and runner.
- Every start or resume is a new **attempt** with its own resources: `job-<id>-a<n>-h2o`, `job-<id>-a<n>-runner`.
- Slots are released only when the attempt's StatefulSet is confirmed deleted.
- Runner events carry the attempt number. Events from old attempts are ignored (except saved models, which are kept).

## Consequences
- ✅ No name clashes between a dying attempt and a new one.
- ✅ Late events from a dead attempt can't corrupt the current one.
- ✅ Slot accounting matches what the cluster is really running.
- ❌ Preemption frees *all* of a job's slots, even when fewer are needed. Victim choice (SCHEDULING_POLICY §6) keeps this to a minimum.
- ❌ Every resume pays H2O startup time again. Measured at only **~7 s** for a 2-node cloud with the image preloaded.

## Validation (Spike A, 2026-11-14)
| Check | Result |
|---|---|
| Multi-pod cluster forms on kind via headless Service | ✅ 2-node cloud in ~7 s |
| Losing a pod breaks the cluster | ✅ Confirmed. Replacement crash-loops: "cloud … no longer accepting new H2O nodes" |
| Startup time | ~7 s create → cloud formed. Runner connected 6.5 s after start |

Extra finding: a restarted leader comes back as a **healthy-looking 1-node cloud**. The reconciler treats any H2O pod restart as a dead attempt and ignores readiness. Details: [SPIKE_RESULTS §1](../SPIKE_RESULTS.md#1-spike-a-h2o-cluster-on-kind).

## Alternatives considered
| Option | Why not |
|---|---|
| **Shrink the cluster** | H2O clusters can't shrink |
| **Reuse fixed resource names** | New attempt clashes with old pods still terminating |
| **Release slots on state change** | Scheduler would start jobs on capacity still in use; ResourceQuota would block them |
