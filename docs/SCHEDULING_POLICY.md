# Fairshare — Scheduling Policy

**Status:** Draft v1 (Phase 0)
**Last updated:** 2026-11-14

Related docs: [PRODUCT_SPEC](PRODUCT_SPEC.md) · [JOB_LIFECYCLE](JOB_LIFECYCLE.md) · [ARCHITECTURE](ARCHITECTURE.md)

---

## 1. Goals, in order

1. **Never overcommit.** Slots in use ≤ cluster capacity, always.
2. **Priority first.** A higher-priority job is never kept waiting for a lower-priority one.
3. **Fair share within a priority.** Among jobs of the same priority, the tenant furthest below its share goes next.
4. **No idle capacity** while jobs wait, as long as it doesn't starve a big job (§5).
5. **Predictable.** Same input → same decision. Every waiting job has a stated reason.

---

## 2. Inputs

The scheduler reads one consistent snapshot from Postgres each tick.

| Symbol | Meaning |
|---|---|
| `C` | Cluster capacity in slots (config, local default 4) |
| `w_t` | Tenant weight |
| `m_t` | Tenant max slots |
| `u_t` | Slots tenant *t* holds now (attempts with `released_at` null) |
| `U` | Total slots held = Σ `u_t` |
| `free` | `C − U` |
| queued jobs | Jobs in `PENDING` or `PREEMPTED` |

**Active tenants** = tenants with at least one queued or slot-holding job. Only active tenants count toward shares. An idle tenant doesn't reserve anything.

---

## 3. Fair share

### Formula
1. Start with `s_t = C × w_t / Σ w_active`.
2. If `s_t > m_t`: cap at `m_t`, and give the excess to the other uncapped active tenants in proportion to their weights.
3. Repeat step 2 until no share exceeds its cap.

### Share ratio
- `ratio_t = u_t / s_t`
- Lower ratio = further below fair share = goes first.
- `ratio_t > 1` means the tenant is **borrowing**: using more than its share because capacity was idle.

### Example
`C = 4`. Fraud weight 4, marketing 3, risk 3. All active.

| Tenant | Share `s_t` | Using `u_t` | Ratio | |
|---|---|---|---|---|
| fraud | 4 × 4/10 = 1.6 | 2 | 1.25 | borrowing |
| marketing | 1.2 | 2 | 1.67 | borrowing |
| risk | 1.2 | 0 | 0.00 | **next in line** |

---

## 4. Picking the next job

The scheduler runs every **2 s** on the leader, and also right after any slot is released.

### Step 1: each tenant's candidate
- Within a tenant, order queued jobs by: **priority** (high first) → **`submitted_at`** (oldest first) → `id`.
- The first one is that tenant's candidate.

### Step 2: order candidates across tenants
Sort key, in order:
1. **priority**, high first
2. **ratio**, low first
3. **`submitted_at`**, oldest first
4. **job id**, as a final tie-break

### Step 3: place
Walk the sorted candidates. For each one:
- **Fits** (`size ≤ free` and `u_t + size ≤ m_t`) → start it (T1/T2), update `free` and `u_t`, recompute ratios, go back to Step 1.
- **Doesn't fit** → record its wait reason, then try the next candidate (backfill), unless a reservation blocks it (§5).

Stop when nothing else fits.

### Why priority beats fairness
- `urgent` must mean urgent across the whole cluster.
- Abuse is limited by the **1 urgent job per tenant** rule (FR-8).
- `low` is for background work. It runs only when no `normal` or `urgent` job is waiting for the same capacity.

---

## 5. Reservations (stopping big jobs from starving)

### The problem
A `large` job needs 4 slots. If small jobs keep backfilling every slot that frees up, the large job never runs.

### The rule
- A candidate that doesn't fit and has been waiting **≥ 10 min** gets a **reservation**.
- While a reservation exists, no job of **equal or lower priority** may start, except the reserved job itself.
- Freed slots accumulate until the reserved job fits.
- Only **one** reservation exists at a time: the highest-priority, oldest-waiting candidate.
- A higher-priority job can still start, but the reservation stays.

### Preemption reservations
- When the scheduler preempts victims for job J (§6), J gets a reservation right away (no 10-minute wait).
- This guarantees the slots freed by preemption go to J, not to another job.

---

## 6. Preemption

### When
After placement (§4), for the **highest-priority** candidate J that still doesn't fit, when all of these hold:
- J is `normal` or `urgent`
- `u_J + size_J ≤ m_J` (J's tenant has room under its cap)
- there are enough **eligible victims** to fit J
- no other preemption is already in progress for J

### Eligible victims
A running job V is eligible only if all of these hold:
- V's priority is **strictly lower** than J's (FR-14)
- V is in `RUNNING`. Not `PROVISIONING`, which has no work to save, and not already `CHECKPOINTING`
- `V.preemption_count < 2` (FR-16)

### Choosing victims
Sort eligible victims by:
1. **borrowing first**: V's tenant has `ratio > 1`
2. **priority**, low first
3. **`started_at`** of the current attempt, newest first (loses the least work)

Take victims from the top until `free` + their slots + slots already being freed (jobs in `CHECKPOINTING`/teardown) ≥ `size_J`.

**Minimality check:** after picking, drop any victim whose removal still leaves enough room. This avoids preempting more than needed.

### Effects
- Each victim: `RUNNING` → `CHECKPOINTING` (T5), `preemption_count += 1`.
- J: gets a preemption reservation (§5) and wait reason `WAIT_PREEMPTION`.
- When the victims' slots are released, the normal placement in §4 starts J.

### At most one preemption round at a time
- If any job is still `CHECKPOINTING` because of an earlier round, no new victims are chosen that tick.
- This prevents cascades where every tick preempts something new.

---

## 7. Wait reasons

Every queued job shows one reason, updated each tick.

| Code | Shown as |
|---|---|
| `WAIT_CAPACITY` | "Waiting for N free slots" |
| `WAIT_TENANT_MAX` | "Your team is at its max of N slots" |
| `WAIT_PRIORITY` | "Higher-priority jobs are waiting" |
| `WAIT_FAIR_SHARE` | "Other teams are below their fair share" |
| `WAIT_RESERVATION` | "Slots are being held for job #N" |
| `WAIT_PREEMPTION` | "Freeing slots: pausing job(s) #N" |
| `WAIT_SCHEDULER` | "Waiting for next scheduling tick" (newly submitted) |

---

## 8. Worked example (the demo scenario)

`C = 4`. Weights: fraud 4, marketing 3.

| Time | Event | Decision |
|---|---|---|
| 0:00 | Marketing submits job M (`large`, `normal`) | Only marketing active → share 4. M starts (4 slots) |
| 1:00 | Fraud submits job F (`medium`, `urgent`) | `free = 0`. F is `urgent`, M is `normal` → M eligible. Preempt M → `CHECKPOINTING`. F reserved |
| 1:01 | M checkpointed, resources deleted, 4 slots free | F fits → F starts (2 slots). M (`PREEMPTED`, `large`) doesn't fit in the 2 free slots → `WAIT_CAPACITY` |
| 1:31 | F succeeds, slots released | M fits → M resumes (attempt 2), skipping its finished models |

---

## 9. Starvation and known trade-offs

| Case | Behavior | Why accepted |
|---|---|---|
| `low` jobs under constant `normal` load | Can wait forever | `low` means "only when idle." Documented in the UI |
| A tenant with weight 1 vs weight 9 | Gets ~10% when both are busy | That's the configured policy |
| Job bigger than its tenant's share | Runs only when others leave room, or via a reservation after 10 min | Reservation guarantees progress |
| Preempted twice | Can't be preempted again | Bounds wasted work (FR-16) |

---

## 10. Implementation shape

- **Pure function** at the core: `Plan(snapshot) → []Decision`.
  - Decisions: `Start(job)`, `Preempt(victim, for: job)`, `Reserve(job)`, `SetWaitReason(job, code)`.
  - No database access, no clock calls inside. The snapshot includes "now".
- A thin loop around it: load snapshot → `Plan` → apply decisions in **one transaction** using compare-and-set updates.
- If the transaction conflicts (a job changed state meanwhile), drop the plan and re-plan next tick.

### Testing
- **Table tests** for every rule in this doc, one case per rule.
- **Property tests:** for random snapshots, check:
  - never over capacity
  - never over a tenant's max
  - no higher-priority job waits while a lower one starts in the same tick
  - victims always strictly lower priority
- **Simulation** with the fake runner: success scenario 3 in [PRODUCT_SPEC §9](PRODUCT_SPEC.md#9-success-criteria-demo-scenarios), usage within ±10% of shares.

---

## 11. Configuration

| Setting | Default | Notes |
|---|---|---|
| `capacity_slots` | 4 | Must match the ResourceQuota |
| `slot_cpu` / `slot_memory` | 1 / 1.5 GiB | Per H2O pod. Heap = 60% (SPIKE_RESULTS F5) |
| `tick_interval` | 2 s | |
| `reservation_after` | 10 min | |
| `max_preemptions_per_job` | 2 | |
| `max_urgent_per_tenant` | 1 | Pending + running |

Changes take effect on the next tick. Each change is audited.
