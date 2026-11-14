# ADR-0004: Checkpoint per finished model

**Status:** Accepted, validated by Spike B · **Date:** 2026-11-14

## Context
- Preempted jobs must resume without starting over (FR-18).
- H2O AutoML has **no pause/resume**. A running AutoML job can only be cancelled.
- H2O can save and load individual trained models.

## Decision
- The unit of saved work is **one finished model**.
- The runner saves each model to object storage as soon as it appears on the leaderboard and records it in `job_models`.
- On resume, the runner loads saved models and runs AutoML only for the **remaining** model count and runtime.
- The final leaderboard combines loaded and new models.

## Consequences
- ✅ Works with H2O as it is. No changes to H2O.
- ✅ A preemption loses at most the one model in progress.
- ✅ Resume does no duplicate work: the runner passes an explicit `modeling_plan` on every attempt, and on resume removes the finished steps.
- ❌ StackedEnsemble is excluded, since an ensemble depends on other models.
- ❌ Saving every model costs some time and storage. Small models are typically a few MB.

## Validation (Spike B, 2026-11-14)
| Check | Result |
|---|---|
| Model saved from cluster A loads into new cluster B | ✅ 3/3 models scored on validation data |
| Combined leaderboard from loaded + new models | ✅ `h2o.make_leaderboard`, 8 models |
| AutoML skips finished steps | ✅ `modeling_plan` ran exactly the 5 remaining steps |

### How finished steps are identified
- After a run (or a cancel), the AutoML object's `modeling_steps` lists the steps that ran, in order.
- The nth saved model of a family (`GBM_n`, `DRF_n`, `XRT_n`, `GLM_n`) came from the nth executed step of that family.
- Resume plan = full plan − those steps.

Details: [SPIKE_RESULTS §2](../SPIKE_RESULTS.md#2-spike-b-checkpoint-and-resume).

## Alternatives considered
| Option | Why not |
|---|---|
| **No resume, restart from zero** | Defeats the main feature |
| **Pause the H2O cluster in place** (scale to 0, keep state) | H2O keeps state in memory only. Scaling down destroys it |
| **Mid-model checkpoints** | Only some algorithms support it, and not through AutoML |
