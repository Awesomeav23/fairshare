# ADR-0005: Preemption deletes the whole H2O cluster; resources are per attempt

**Status:** Accepted, pending Spike A · **Date:** 2026-11-14

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
- ❌ Every resume pays H2O startup time again (expected 30–60 s; Spike A measures it).

## Validation (Spike A)
Must show:
1. A multi-pod H2O cluster forms on kind through a headless Service.
2. Deleting one pod breaks the cluster (confirms the assumption).
3. Startup time from StatefulSet create to "cluster ready."

## Alternatives considered
| Option | Why not |
|---|---|
| **Shrink the cluster** | H2O clusters can't shrink |
| **Reuse fixed resource names** | New attempt clashes with old pods still terminating |
| **Release slots on state change** | Scheduler would start jobs on capacity still in use; ResourceQuota would block them |
