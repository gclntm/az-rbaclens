# Redesign plan — UX and data layer

Status: **shipped** (phases 1–5, 2026-10-22); next: [plan-who-can-do-what.md](plan-who-can-do-what.md). Mockup: made during design, not kept.

This document records the assessment of the app as of 2026-10-22, the
proposed redesign, and the database work that
goes with it. It is the plan of record until each phase lands; tick the
boxes as they ship and move the corresponding line into the changelog in
`docs/reference.md`.

---

## 1. Assessment

### What works

- The analysis engine is strong: direct vs inherited resolution,
  high-risk / custom-role classification, least-privilege coverage from
  Activity Log, group-transitive membership, scan diff, principal
  search with via-group results, audit log, optional Entra sign-in.
- The design tokens (now docs/design.md) are respected; light/dark both work.
- Every scan is reproducible and persisted; nothing is lost between runs.

### What hurts

The UI exposes the implementation (scans) rather than the questions a
reviewer asks ("who can touch prod?", "what does alice have?", "what is
risky?", "what changed?").

| Observed | Impact |
|---|---|
| Nav is **Overview · IAM Access · Principal access · Roles · Events · Settings** | "IAM Access" is a scan list; "Events" is a manual data-fetch chore. No entry says *what is risky* or *what changed*. |
| Everything is keyed by scan id (`/iam-access/41/scan-view`) | No "current state of prod". The same subscription appears 4× in *Recent scans*; *Compare with…* is a dropdown of ids. |
| Start-a-scan is a textarea plus a paragraph of syntax rules; the Browse tab is hidden behind a toggle | Highest-friction entry point in the app. |
| GUIDs dominate tables even where `scope_name` is known | Reviewers read names; ids should be secondary and copyable. |
| Scan-view 3-pane truncates principal and role names at 1440 px | The most-used screen cannot show "Azure Connected Machine Resource Administrator". |
| Coverage: 6 s+ spinner, ~150 px dead space, collapsed "How to read this page" | The best feature is the slowest and least legible. |
| Overview tile "Stage: test — 1" | A KPI slot wasted; no risk signal on the landing page. |
| Delete button on every scan row; permanent orange "Auth: not enabled" badge | Noise in the default path. |

---

## 2. Redesign

**Principle: scope is the hub, snapshots are history, findings are the worklist.**

### Information architecture

```
Overview        risk dashboard, needs-attention feed, scope freshness, job queue
Scopes          tenant tree → scope page (Access · Findings · History · Activity)
Principals      search → principal page (Access · Groups · Activity · Findings)
Roles           catalogue (as today, with usage from latest snapshots only)
Findings        cross-scope worklist with review state
Activity        activity-log coverage per subscription; pulls become scheduled jobs
Jobs            snapshot / pull queue with progress and retry
Settings        credentials · authentication · audit · database · schedules
⌘K              global search over scopes, principals, roles
```

"Scan" is renamed **snapshot** in the UI and lives under a scope's
*History* tab together with diffs. `/iam-access/*` routes stay as
redirects for one release.

### Screens (see mockup boards)

1. **Overview** — five KPIs (assignments, high-risk, unused 90 d,
   unresolved principals, Δ 30 d); *Needs attention* feed; scope tree with
   freshness and high-risk counts; role histogram; recent jobs.
2. **Scope page / Access** — breadcrumb from the hierarchy; name-first
   header with freshness ("Snapshot #41 · 31 days old · Refresh"); KPI
   strip; tabs. Body: 236 px scope tree + full-width table
   (Principal · Role · Scope · Source · Used 90 d) + detail drawer that
   explains *why* a row matters and what to do. Replaces Scan view and
   Inspector.
3. **Scope page / Findings** — the coverage analysis re-cut as a
   prioritised list: severity, category chip, one-line evidence,
   *Suggested* action, Mark reviewed / Snooze; review progress and
   methodology in the rail. Replaces Coverage.
4. **Principal page** — header with group count and last ARM activity;
   KPIs; access grouped by subscription with Direct / Via group; findings
   and top operations in the rail. Replaces the search-results card.

### UI rules carried over

- Design tokens unchanged (Inter, `#2563eb`, 8 px grid, 44 px targets).
- Names first, ids as `mono` secondary text with a copy button.
- Every table row has a drawer, not a modal; drawers link to the
  principal / role / scope pages.
- Destructive actions (delete snapshot) live in a `⋯` menu, not inline.

### New backend concepts the UI needs

| Concept | Why |
|---|---|
| `scopes` registry with `latest_done_scan_id` | "current state of prod", breadcrumbs, freshness in one query |
| `principals` registry | principal page, name-first tables, ⌘K search |
| `findings` persisted per snapshot | Findings tab is a read, not a 6 s recompute |
| `review_decisions` | Mark reviewed / snooze / accept risk survives re-scans |
| `jobs` (snapshot, pull, scheduled) | Jobs page, scheduled activity pulls, retry |

---

## 3. Database plan

Based on a code-level audit of `backend/app/{api,core,tools,db}` on
2026-10-22. File:line references are to the code as it was then.

### P0 — correctness (ship first, no UI change)

All six shipped in migration `0016` (2026-10-22):
`app/core/jobs.py`, `app/core/sweeper.py`, `tests/test_job_lifecycle.py`.

- [x] **Failure path leaves scans stuck `running`.** On a flush error the
  `except` writes `status="failed"` into a session already in a failed
  transaction; the `finally` commit raises `PendingRollbackError` and the
  row stays `running` until the sweeper flips it an hour later
  (`tools/iam_access.py:140-146`, same shape in `tools/activity.py:83-89`).
  Fix: `session.rollback()` before recording the failure; write the
  failure in a fresh transaction.
- [x] **"Latest scan" ignores status.** `_latest_scan_ids_per_root`
  (`api/iam_access.py:160-183`) takes `max(created_at)` per scope, so a
  failed or still-running scan with 0 RAs wins and blanks role usage.
  Filter `status = 'done'`; break ties on `id`.
- [x] **No guard against concurrent scans of one scope / pulls of one
  subscription.** Two pulls both load the dedupe set, both insert the
  same `event_data_id` → unique violation → bug 1. Add partial unique
  indexes: `scans(scope) WHERE status IN ('pending','running')` and
  `activity_pulls(subscription_id) WHERE status IN ('pending','running')`.
- [x] **ORM cascade delete:** `delete_scan` uses `session.delete` with
  `cascade="all, delete-orphan"` and no `passive_deletes`
  (`api/iam_access.py:289-291`, `db/models.py:56-58`): a 50 k-RA scan is
  a 50 k-row SELECT (with `raw`) plus 50 k DELETEs under lock. Set
  `passive_deletes=True`; the FKs already cascade.
- [x] **Sweeper vs long worker:** scans > `stuck_scan_timeout_seconds`
  are flipped to `failed` while still running; the worker later writes
  `done` over it. Have the worker heartbeat `progress_message`/
  `updated_at` and sweep on staleness, not age.
- [x] No sweeper for `activity_pulls` at all.

### P1 — model (enables the redesign)

- [x] **`scopes`** — shipped in `0018` (`app/core/registry.py`,
  `app/api/registry.py`: `GET /scopes/tree`, `GET /scopes/{id}`; 28 ms for
  the full tenant tree with per-node stats). Spec: `id TEXT PK` (lowercased ARM path), `name`, `level`,
  `parent_id FK`, `resource_type`, `latest_done_scan_id FK`,
  `first_seen_at`, `last_seen_at`. Populated from the discovery walk;
  replaces parsing `scans.hierarchy` JSONB in Python on every request
  (`api/iam_access.py:344-351, 937-942`). `scans.hierarchy` stays for
  one release as a fallback, then drops.
- [x] **`principals`** — shipped in `0018` (`GET /principals`,
  `GET /principals/{id}`; ids lowercased at ingest). Spec: `id` (object id, lowercased, `CHAR(36)`),
  `display_name`, `upn`, `type`, `resolved_at`, `resolve_error`,
  `last_activity_at`. `role_assignments.principal_name` and
  `group_memberships.member_name` become derived; ids are normalised
  once at ingest, removing the `lower()` wrappers that defeat every
  index in `/principals` (`api/iam_access.py:1174-1208`).
- [ ] **`role_assignments.role_definition_id`** → bare GUID with an FK
  to `role_definitions`; drop the duplicated `role_name` / `role_type`
  columns. Today the column holds the full ARM path (`Text`), the PK on
  the other side is the GUID, and every lookup strips it in Python
  (`api/iam_access.py:124-126, 504, 598`).
- [ ] *(Measured 2026-10-22: small at today's volume; `CurrentView` defers it.
  Not worth a migration yet; revisit at ~100 k RAs.)* **Drop
  `role_assignments.raw`.** No endpoint reads it; at ~1 KB it
  sits inline (below TOAST) and inflates every seq scan and ORM load.
  Keep Azure's assignment id in a real column (`azure_id`) with a unique
  `(scan_id, azure_id)` index; dedupe is currently in-memory only.
- [x] **Trim `activity_events.raw`** — shipped in `0022`: `caller_oid`
  column + `(caller_oid, occurred_at)` index; `raw` slimmed in place
  (`app/core/activity_payload.py`), which shrank the table substantially. Original note: only `claims.oid` is read
  (`core/role_coverage.py:311-322`); the column stores 2–8 KB per event.
  Store `caller_oid` as a column and drop `raw`, or keep a slimmed
  `claims` JSONB.
- [x] **`findings`** — shipped in `0017` (`app/core/findings.py`,
  `app/api/findings.py`, `tests/test_findings.py`); coverage builder moved
  to `app/core/scan_coverage.py` and cached in `scan_coverage` (29 ms vs
  2 s on scan #41). Original spec: `id`, `scan_id FK`, `role_assignment_id FK`,
  `category` (high_risk | unused | redundant | unresolved_principal |
  custom_replaceable | drift | membership_unknown), `severity`,
  `evidence JSONB`, `suggestion`, `fingerprint` (stable across scans:
  `sha(scope, role_def, principal, category)`). Written at the end of
  the coverage computation, which moves from request time to the end of
  each snapshot job (and re-runs when a new activity pull lands).
- [x] **`review_decisions`** — shipped in `0017`. `fingerprint PK`, `decision`
  (reviewed | accepted_risk | snoozed_until | resolved), `actor`, `note`,
  `decided_at`. Keyed by fingerprint, not finding id, so decisions
  survive re-scans and re-classification.
- [x] **Schedules** (0020) — `job_schedules` (`kind`, `target`,
  `every_hours`, `enabled`, `next_run_at`, …) drive the Jobs page and
  replace the manual Events pull form. *Shipped differently from the
  draft:* an interval instead of `cron` (every use so far is "every N
  hours"), and `GET /jobs` merges `scans` + `activity_pulls` instead of
  a generalised `jobs` table; both already carry status, heartbeat and
  error, and the in-flight unique indexes guard them.
- [ ] **`jobs` table** — generalise `scans` + `activity_pulls` status
  columns (`attempt`, `not_before` for retry with backoff). Only worth
  it once automatic retry is wanted; the scheduler's thread cap covers
  the concurrency half of the pool item below.
- [ ] CHECK constraints (or PG enums) on `scans.status`, `scope_level`,
  `principal_type`, `role_type`, `activity_events.status`,
  `caller_type`. Consistent id widths (`principal_id String(255)` vs
  `group_id String(64)` for the same GUIDs today).

### P2 — performance

*Measured 2026-10-22 on the production data: every list /
detail endpoint answers in < 0.3 s, so the unchecked items below wait
for data that needs them. The volume was all in `activity_events`.*

- [x] ~~`/principals` and the four cross-scan `lower(col) IN (...)` queries~~ —
  obsolete: the registry (`0018`) serves `/principals`; coverage aggregates in SQL.
  in coverage (`api/iam_access.py:851-890`) are full seq scans. P1
  `principals` makes them PK lookups; interim: expression indexes on
  `lower(principal_id)`, `lower(principal_name)`, `lower(group_id)`,
  `lower(member_id)`.
- [ ] N+1 in `GET /scans` and `/dashboard/stats` (two `count()` per scan);
  `GET /role-definitions/{id}/usage` loads every RA of every latest scan
  and filters by GUID in Python (`:547-627`). Rewrite as
  `COUNT(DISTINCT …) GROUP BY` / `WHERE role_definition_id = …`.
- [x] Events export streams (server-side cursor, 5 k-row chunks, never
  `raw`). `GET /scans/{id}/role-assignments` still has no LIMIT: it is fast and
  small even for the largest snapshot, so it waits.
- [ ] Progress updates are 3 round-trips per scope (commit → expire →
  reload; `tools/iam_access.py:102-106`). Batch every 25 scopes and use
  `session.execute(update(...))` without expiring the Scan instance.
- [ ] Bulk inserts: `session.add()` per row for RAs, memberships and
  role-definition upserts → `insert(...).values([...])` with
  `ON CONFLICT`.
- [ ] Dashboard aggregates (`GROUP BY role_name`, `GROUP BY scope`) run
  across all scans, inflating counts by the number of repeat scans.
  Restrict to `scopes.latest_done_scan_id`.
- [x] `0022` dropped `ix_activity_events_caller`,
  `ix_activity_events_caller_time` and `ix_activity_events_operation_name`
  (large, and never used: the filters are `LIKE '%x%'`).
- [x] Retention (`app/core/retention.py`, daily + Settings → Database):
  events 120 d, newest 6 snapshots per scope (never one a scope still
  points to), failed snapshots 7 d, audit 1 y. `VACUUM FULL` from the UI.
- [x] *(Partly, phase 3: scheduled jobs share a pool of
  `SCHEDULER_MAX_CONCURRENT_JOBS` threads; manual one-offs still use
  `BackgroundTasks`.)* Pool: default 5 + 10 overflow vs unbounded `BackgroundTasks`
  threads. Cap concurrent jobs (the `jobs` table gives a natural queue)
  and size the pool to match.

---

## 4. Sequencing

| Phase | Scope | Depends on | Notes |
|---|---|---|---|
| 1 ✅ | P0 fixes; `findings` + `review_decisions`; coverage computed at snapshot end | — | Shipped 2026-10-22 (`0016`, `0017`). No UI change; Coverage tab now served from cache. |
| 2 ✅ | `scopes`, `principals` (`0018`, `0019`); Overview + Scope page (boards 1–2) | 1 | Shipped 2026-10-22. Old `/iam-access/*` routes redirect; Scan view and Inspector retired; Coverage kept until phase 3. |
| 3 | Findings tab (board 3) ✅ + cross-scope `/findings` ✅; Coverage retired ✅; schedules + Jobs page ✅; Events page → Activity + Jobs ✅ | 1, 2 | ✅ Part 1 and part 2 shipped 2026-10-22 (part 2: `job_schedules` + scheduler loop, incremental scheduled pulls, `/jobs` page, manual pull form moved off Activity). |
| 4 | Principal page ✅ + ⌘K ✅ (board 4); browse-tree replaces the scan textarea ✅ | 2 | ✅ Shipped 2026-10-22: `/principals/:id` (Access · Groups · Activity · Findings), registry-backed `/principals` list, `GET /search` + palette, snapshot picker (MG → sub → RG from Azure, registry fallback); 0021 caller-oid index; group membership taken from the newest scan that resolved the group. |
| 5 | P2 perf; drop `raw` columns and `scans.hierarchy` | 2, 3 | ✅ Shipped 2026-10-22 after measuring: `activity_events` was nearly all of the DB → `0022` (`caller_oid`, slim `raw`, index cleanup), retention job, streamed export, `VACUUM FULL`. Not done, by measurement: `role_assignments.raw` and `scans.hierarchy` (both small; the latter still read by the registry rebuild). |

Each phase is one Alembic migration chain and one PR; phases 2–4 keep
the previous UI reachable behind redirects until the phase after lands.

### Open decisions

1. ~~`review_decisions` keyed per finding fingerprint (proposed) vs per
   role assignment.~~ **Decided 2026-10-22: fingerprint.** Shipped in
   `0017`.
2. ~~Keep any of `activity_events.raw`?~~ **Decided 2026-10-22: slim it** —
   keep the drawer-relevant keys and a claims subset; `caller_oid` column.
3. ~~Snapshot retention default.~~ **Decided 2026-10-22: last 6 per scope,
   failed after 7 days, events 120 days, one `VACUUM FULL`.**
