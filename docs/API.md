# Fairshare — REST API

**Status:** Draft v1 (Phase 0)
**Last updated:** 2026-11-14

Related docs: [PRODUCT_SPEC](PRODUCT_SPEC.md) · [DATA_MODEL](DATA_MODEL.md) · [JOB_LIFECYCLE](JOB_LIFECYCLE.md)

An OpenAPI file (`docs/openapi.yaml`) is added in Phase 1 and kept in sync with this doc. CI checks that the server matches it.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Base path | `/api/v1` |
| Format | JSON, except dataset upload (multipart) and model download (binary) |
| Auth | `Authorization: Bearer fs_<token>` on every endpoint except `/healthz` and `/readyz` |
| IDs | Tenants, users, datasets, tokens: UUID. Jobs: integer |
| Times | RFC 3339, UTC |
| Field names | `snake_case` |
| Pagination | Cursor-based: `?limit=50&cursor=<opaque>`. Response has `next_cursor` (null at the end). Max `limit` 200 |
| Idempotency | `POST /jobs` accepts `Idempotency-Key`. Same key + same user → the original job is returned, not a new one |

### Error format
```json
{
  "error": {
    "code": "job_invalid_transition",
    "message": "Job 42 is SUCCEEDED and cannot be cancelled.",
    "details": { "job_id": 42, "state": "SUCCEEDED" }
  }
}
```

### Status codes
| Code | When |
|---|---|
| 200 / 201 / 202 | OK / created / accepted (async work started) |
| 400 | Validation failed (`details` lists fields) |
| 401 | Missing, unknown, revoked token, or disabled user |
| 403 | Valid token, wrong role or wrong tenant |
| 404 | Not found, **or exists in another tenant** (never reveal other tenants' IDs) |
| 409 | Conflict: illegal state change, duplicate name, urgent limit reached |
| 413 | Upload larger than 100 MB |
| 422 | Well-formed but impossible (e.g. job size > tenant max slots) |
| 500 | Server error. Body has a `request_id` for logs |

---

## 2. Endpoint summary

Roles: **A** = admin, **M** = tenant-member (own tenant only), **Au** = auditor.

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/healthz` | none | Process is up |
| GET | `/readyz` | none | Postgres, RabbitMQ, MinIO reachable |
| GET | `/me` | A M Au | Current user, role, tenant |
| GET | `/tenants` | A Au | List tenants |
| POST | `/tenants` | A | Create tenant |
| GET | `/tenants/{id}` | A Au | Get tenant |
| PATCH | `/tenants/{id}` | A | Change weight / max slots |
| GET | `/users` | A | List users |
| POST | `/users` | A | Create user |
| PATCH | `/users/{id}` | A | Disable / enable |
| GET | `/users/{id}/tokens` | A | List tokens (prefix only) |
| POST | `/users/{id}/tokens` | A | Issue token (shown once) |
| DELETE | `/tokens/{id}` | A | Revoke token |
| POST | `/datasets` | M | Upload CSV |
| GET | `/datasets` | A M Au | List datasets |
| GET | `/datasets/{id}` | A M Au | Get dataset |
| DELETE | `/datasets/{id}` | M | Soft delete |
| POST | `/jobs` | M | Submit job |
| GET | `/jobs` | A M Au | List jobs |
| GET | `/jobs/{id}` | A M Au | Get job |
| GET | `/jobs/{id}/timeline` | A M Au | State history |
| GET | `/jobs/{id}/leaderboard` | A M Au | Leaderboard |
| GET | `/jobs/{id}/model` | A M | Download best model (MOJO) |
| POST | `/jobs/{id}/cancel` | A M | Cancel |
| GET | `/cluster` | A M Au | Capacity, usage, shares, queue |
| GET | `/settings` | A Au | Scheduler settings |
| PATCH | `/settings` | A | Change settings |
| GET | `/audit` | A Au | Browse audit log |
| POST | `/audit/verify` | A Au | Verify hash chain |
| GET | `/admin/dead-letters` | A | Messages that failed 5 times |

---

## 3. Endpoints in detail

### `GET /me`
```json
{ "id": "6f1c…", "name": "priya", "role": "tenant_member",
  "tenant": { "id": "a3e9…", "name": "fraud" } }
```

### `POST /tenants`
Request:
```json
{ "name": "fraud", "weight": 4, "max_slots": 4 }
```
- `name`: 2–40 chars, lowercase letters, digits, `-`.
- `max_slots` ≤ `capacity_slots`.
- 201 → tenant object. 409 `tenant_name_taken`.

### `PATCH /tenants/{id}`
```json
{ "weight": 5 }
```
- Takes effect on the next scheduler tick. Running jobs are not preempted just because a weight changed.

### `POST /users`
```json
{ "name": "priya", "role": "tenant_member", "tenant_id": "a3e9…" }
```
- 400 if `tenant_id` is given for admin/auditor, or missing for tenant_member.

### `POST /users/{id}/tokens`
Request: `{ "label": "laptop" }`

Response 201. **The `token` field appears only here, once:**
```json
{ "id": "c71d…", "label": "laptop", "prefix": "fs_a1b2c",
  "token": "fs_a1b2c3d4…", "created_at": "2026-11-14T19:02:11Z" }
```

### `POST /datasets`
- `multipart/form-data`, one field `file`.
- Checks: `.csv`, ≤ 100 MB, header row present, at least 2 columns and 10 data rows.
- 201:
```json
{ "id": "d4b2…", "name": "credit_default.csv", "size_bytes": 2841123,
  "row_count": 30000, "columns": ["limit_bal", "age", "…", "default"],
  "created_at": "…" }
```
- 409 `dataset_name_taken` (per tenant). 413 `dataset_too_large`.

### `DELETE /datasets/{id}`
- 409 `dataset_in_use` if any unfinished job uses it.

### `POST /jobs`
Headers: optional `Idempotency-Key: <string ≤ 100 chars>`

Request:
```json
{
  "dataset_id": "d4b2…",
  "target_column": "default",
  "size": "medium",
  "priority": "normal",
  "max_models": 10,
  "max_runtime_secs": 900,
  "seed": 42
}
```
Defaults: `size` = `small`, `priority` = `normal`, `max_models` = 10, `max_runtime_secs` = 900.

| Check | Error |
|---|---|
| Dataset not found / other tenant | 404 `dataset_not_found` |
| Column not in dataset | 400 `target_column_unknown` |
| Size > tenant `max_slots` or capacity | 422 `job_too_large` |
| Tenant already has an unfinished urgent job | 409 `urgent_limit_reached` |

Response 201 (or 200 if the idempotency key matched an existing job): job object (below).

### Job object
Returned by `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`.
```json
{
  "id": 42,
  "tenant": { "id": "a3e9…", "name": "fraud" },
  "dataset": { "id": "d4b2…", "name": "credit_default.csv" },
  "created_by": { "id": "6f1c…", "name": "priya" },
  "target_column": "default",
  "size": "medium",
  "slots": 2,
  "priority": "normal",
  "max_models": 10,
  "max_runtime_secs": 900,
  "seed": 42,
  "state": "RUNNING",
  "current_attempt": 2,
  "wait": null,
  "progress": { "models_completed": 6, "runtime_used_secs": 412 },
  "best": { "model_id": "GBM_3_AutoML_…", "metric": { "name": "auc", "value": 0.781 } },
  "preemption_count": 1,
  "failed_attempts": 0,
  "error": null,
  "submitted_at": "…",
  "first_started_at": "…",
  "finished_at": null
}
```
While queued, `wait` is set:
```json
"wait": { "code": "WAIT_CAPACITY", "message": "Waiting for 2 free slots", "detail": { "slots_needed": 2 } }
```

### `GET /jobs`
Filters: `state` (repeatable), `tenant_id` (admin/auditor only), `priority`, `created_by`. Sorted by `submitted_at` desc.

### `GET /jobs/{id}/timeline`
```json
{ "items": [
  { "at": "…", "attempt": 1, "from": null, "to": "PENDING", "actor": "user:6f1c…", "reason": null },
  { "at": "…", "attempt": 1, "from": "PENDING", "to": "PROVISIONING", "actor": "system:scheduler", "reason": null },
  { "at": "…", "attempt": 1, "from": "RUNNING", "to": "CHECKPOINTING", "actor": "system:scheduler", "reason": "preempted for job #51" }
] }
```

### `GET /jobs/{id}/leaderboard`
- Available once at least one model is saved. During a run this shows checkpointed models; after success, the final combined leaderboard.
```json
{ "final": false, "metric": "auc",
  "models": [ { "model_id": "GBM_3_…", "algo": "GBM", "metric_value": 0.781, "attempt": 1 } ] }
```

### `GET /jobs/{id}/model`
- Only when `state = SUCCEEDED`. Otherwise 409 `model_not_ready`.
- Streams `application/zip` with `Content-Disposition: attachment; filename="job-42-best.zip"`.

### `POST /jobs/{id}/cancel`
- 202 → job object with state `CANCELLING` or `CANCELLED`.
- Already final → 409 `job_invalid_transition`.
- Repeated cancel on a `CANCELLING` job → 202, no change (idempotent).

### `GET /cluster`
```json
{
  "capacity_slots": 4,
  "used_slots": 4,
  "tenants": [
    { "id": "…", "name": "fraud", "weight": 4, "max_slots": 4,
      "fair_share": 1.6, "used_slots": 2, "ratio": 1.25, "queued_jobs": 1 },
    { "id": "…", "name": "marketing", "weight": 3, "max_slots": 4,
      "fair_share": 1.2, "used_slots": 2, "ratio": 1.67, "queued_jobs": 0 }
  ],
  "reservation": { "job_id": 51, "since": "…" },
  "queue": [ { "job_id": 51, "tenant": "risk", "priority": "normal", "slots": 4, "wait_code": "WAIT_RESERVATION" } ]
}
```
- Members see every tenant's numbers (transparency), but `queue` entries from other tenants show only `job_id`, `tenant`, `priority`, `slots` and `wait_code`.

### `PATCH /settings`
```json
{ "reservation_after_secs": 300 }
```
- `capacity_slots` can't be set above the Kubernetes ResourceQuota (checked at startup and on change). 422 otherwise.

### `GET /audit`
Filters: `actor`, `action`, `target_type`, `target_id`, `tenant_id`, `from`, `to`. Sorted by `id` desc.
```json
{ "items": [ { "id": 1043, "at": "…", "actor": "user:6f1c…", "action": "job.cancel",
  "target_type": "job", "target_id": "42", "tenant_id": "a3e9…",
  "details": { "from_state": "RUNNING" } } ],
  "next_cursor": "…" }
```

### `POST /audit/verify`
- Synchronous for v1 (fine up to ~1M rows).
```json
{ "ok": false, "rows_checked": 1043, "first_bad_id": 987,
  "expected_hash": "9f2c…", "actual_hash": "11ab…" }
```

---

## 4. What gets audited

Every successful write endpoint above, plus:
- every job state transition (`job.transition`), including system actors
- token use failures are **not** audited (to avoid flooding), but are logged

Reads are not audited in v1.
