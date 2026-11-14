# ADR-0004: Checkpoint per finished model

**Status:** Accepted, pending Spike B · **Date:** 2026-11-14

## Context
- Preempted jobs must resume without starting over (FR-18).
- H2O AutoML has **no pause/resume**. A running AutoML job can only be cancelled.
- H2O can save and load individual trained models.

## Decision
- The unit of saved work is **one finished model**.
- The runner saves each model to MinIO as soon as it appears on the leaderboard and records it in `job_models`.
- On resume, the runner loads saved models and runs AutoML only for the **remaining** model count and runtime.
- The final leaderboard combines loaded and new models.

## Consequences
- ✅ Works with H2O as it is. No changes to H2O.
- ✅ A preemption loses at most the one model in progress.
- ❌ Resumed AutoML may retrain model *types* already covered, unless we can skip finished steps. Spike B tests the `modeling_plan` parameter for this.
- ❌ Saving every model costs some time and storage. Small models are typically a few MB.

## Validation (Spike B)
Must show:
1. A model saved from cluster A loads into a new cluster B.
2. A combined leaderboard can be built from loaded + new models.
3. AutoML can be told to skip steps already done (nice to have; if not, document the duplicate-type trade-off).

If (1) or (2) fails, revisit this ADR before Phase 1.

## Alternatives considered
| Option | Why not |
|---|---|
| **No resume, restart from zero** | Defeats the main feature |
| **Pause the H2O cluster in place** (scale to 0, keep state) | H2O keeps state in memory only. Scaling down destroys it |
| **Mid-model checkpoints** | Only some algorithms support it, and not through AutoML |
