# Data model

ORM definitions: [`backend/app/db/models/`](../backend/app/db/models/), one
module per area (`checks`, `findings`, `activity`, `registry`, `reviews`,
`settings`), all exported by `app.db.models`. Every time column is
`UTCDateTime` ([`db/types.py`](../backend/app/db/types.py)): it reads back as
an aware UTC time on every engine. JSON columns are `JSONB` on PostgreSQL
and plain JSON on the SQLite test engine.

## Tables at a glance

| Area | Tables |
|---|---|
| Checks (snapshots) | `scans`, `role_assignments`, `role_eligibilities`, `scan_group_resolutions`, `group_memberships`, `role_definitions` |
| Findings | `scan_coverage`, `findings`, `review_decisions` |
| Registries and changes | `scopes`, `principals`, `access_changes` |
| Activity Log | `activity_pulls`, `activity_events`, `activity_compacted_days`, `operation_names` |
| Entra ID audit log | `directory_audit_events` |
| Reviews and follow-up | `responsibilities`, `responsible_suggestions`, `review_rounds`, `review_items`, `todos`, `assessments`, `notifications`, `ai_texts` |
| Settings and jobs | `azure_credentials`, `auth_settings`, `app_settings`, `job_schedules`, `audit_events` |

## Checks

```
┌────────────────────────────────────────────────────────────────┐
│ scans                         one per check                    │
│  id                  PK                                        │
│  scope               TEXT        full ARM path (lowercased)    │
│  scope_level         VARCHAR(20) mgmt_group | subscription |   │
│                                  resource_group | resource     │
│  status              VARCHAR(20) pending|running|done|failed   │
│  started_at, finished_at, error, created_at                    │
│  progress_total, progress_done, progress_message               │
│  heartbeat_at        bumped on every progress update (sweeper) │
│  hierarchy           JSONB  {scope: {name, level, parent, …}}  │
│  eligibility_status  read | failed | NULL (before 0024)        │
│  eligibility_error                                              │
│  origin              schedule | manual | NULL (before 0037)     │
│  schedule_id         not an FK: deleting a schedule keeps jobs │
└────────────────────────────────────────────────────────────────┘
                              │ 1:N  ON DELETE CASCADE
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ role_assignments              one per Azure role assignment    │
│  id, scan_id         FK → scans                                │
│  scope, scope_name, scope_level                                │
│  principal_id        lowercased object id                      │
│  principal_name, principal_type                                │
│  principal_app_owner, principal_sp_type   apps only (0031)     │
│  role_definition_id, role_name, role_type                      │
│  condition, condition_version             ABAC (0023)          │
│  assignment_type     Assigned | Activated (PIM), end_at (0024) │
│  created_on, created_by, created_by_name, created_by_type,     │
│  updated_on          Azure's own record (0025)                 │
│  raw                 JSONB, deferred: Azure's full record      │
│  IX (scan_id, scope) · (scan_id, role_name) ·                  │
│     (scan_id, principal_id)                                    │
└────────────────────────────────────────────────────────────────┘
```

```
role_eligibilities     id, scan_id → scans (CASCADE), azure_id (unique per scan),
                       scope, scope_name, scope_level, principal_*, role_definition_id,
                       role_name, role_type, member_type (Direct | Group | Inherited),
                       condition, condition_version, start_at, end_at (NULL = no end), status
                       IX (scan_id, scope); IX (scan_id, principal_id)
scan_group_resolutions id, scan_id → scans (CASCADE), group_id; UNIQUE (scan_id, group_id)
group_memberships      id, scan_id → scans (CASCADE), group_id, member_id, member_type,
                       member_name; UNIQUE (scan_id, group_id, member_id)
role_definitions       role_definition_id PK (bare GUID, lowercased), role_name, role_type,
                       description, permissions JSON, assignable_scopes JSON, fetched_at
```

- **`hierarchy` is JSON, not a table.** Each check keeps the tree it
  walked (hundreds of nodes at most), keys and parents lowercased. Places
  Azure refused are flagged `unread` there (`unread_scopes` in the API):
  their grants are unknown, not absent.
- **Group members:** a `scan_group_resolutions` row means Graph answered
  for the group (possibly with no members); no row means it was refused or
  never asked. Both give zero `group_memberships` rows; the resolution row
  tells them apart.
- **`role_definitions` is a global cache.** Filled during checks, shared
  by all of them, and kept when a check is deleted.
- **Eligibilities** (PIM) are per check and follow the same "current" rule
  as grants. `assignment_type` / `end_at` say whether an active grant is
  standing or an activated eligibility.
- **Origin** (`created_on`, `created_by`, …) comes from the SDK object at
  ingest (`app/tools/grant_origin.py`); the creator's name from the check's
  own name resolution, then Graph. Azure leaves `createdBy` empty on some
  grants.
- **Diff identity** is `(scope, role_definition_id, principal_id)`, not the
  row id: a re-created Azure assignment counts as kept.
- **Entra roles** (`app/tools/entra_directory.py`) are rows of the same two
  tables at the place `/providers/microsoft.aadiam` (`scope_level =
  'directory'`), read by checks of the Tenant Root Group: `role_definition_id`
  is `/providers/microsoft.aadiam/roleDefinitions/<id>`, `raw` keeps Graph's
  assignment id (`source: "entra"`, `directoryScopeId`,
  `principalOrganizationId`); a group of another tenant is typed
  `ForeignGroup` and its members are not asked for. Their definitions
  are in `role_definitions` with `role_type` `EntraBuiltInRole` or
  `EntraCustomRole` and Graph's `rolePermissions` as `permissions`
  (`app/core/entra_roles.py` reads them). The hierarchy carries the place
  as a root node, `unread` when Graph refused.

### Job lifecycle guards (0016)

| Index | Guarantees |
|---|---|
| `uq_scans_inflight_scope` | `UNIQUE (scope) WHERE status IN ('pending','running')`: one check in flight per scope; the API answers 409 |
| `uq_activity_pulls_inflight_subscription` | the same for pulls, per `subscription_id` |

On any exception the worker calls `app.core.jobs.mark_job_failed`: it rolls
back (a failed check never keeps a partial result) and records the failure
in a fresh transaction. The sweeper (`app.core.sweeper`) catches workers
that died outright; it measures staleness from `heartbeat_at`, so a long
check that is still progressing is never flipped.

## Findings and review decisions (0017)

```
scan_coverage        PK scan_id → scans (CASCADE); window_days, computed_at,
                     previous_scan_id → scans (SET NULL), payload JSON
findings             id, scan_id → scans (CASCADE), role_assignment_id →
                     role_assignments (CASCADE), fingerprint, category,
                     severity (high|medium|low), title, detail, suggestion,
                     evidence JSON, computed_at
                     UNIQUE (scan_id, fingerprint); IX (scan_id, severity); IX (fingerprint)
review_decisions     PK fingerprint, decision (reviewed|accepted_risk|snoozed|resolved),
                     note, snoozed_until, actor, actor_name, decided_at
```

`findings` rows are replaced wholesale by `compute_scan_findings` (end of a
check, after a pull on a covered subscription, or recompute). They are not
FK'd to `review_decisions`: the join is by `fingerprint = sha256(scope |
role GUID | principal | category)`, so a decision survives new checks.
Deleting a check removes its findings; decisions are kept.

## Registries and the changes feed

```
scopes               PK id (lowercased ARM path), name, level, parent_id → scopes
                     (SET NULL), resource_type, tags JSON (0027),
                     latest_scan_id → scans (SET NULL), first_seen_at, last_seen_at
principals           PK id (lowercased object id), display_name, principal_type,
                     resolved_at, first_seen_at, last_seen_at, last_activity_at,
                     app_owner_org, sp_type (0031)
access_changes       id, occurred_at, first_seen_scan_id → scans (SET NULL),
                     kind (granted | removed | eligible_added | eligible_removed |
                           member_added | member_removed),
                     source (activity_log | created_on | daily_check | entra_audit),
                     scope, scope_name, scope_level, principal_*, role_*, level,
                     via_group_id, via_group_name, actor_id, actor_name,
                     actor_type (user | app, 0040), direct_group_name (0040), azure_id,
                     activity_event_id → activity_events (SET NULL), subject_key, created_at
                     UNIQUE (subject_key, kind, occurred_at); IX activity_event_id (0039)
```

`app/core/registry.py` maintains `scopes` and `principals` at the end of
every completed check (`INSERT … ON CONFLICT DO UPDATE`):

- **Only walked places claim a check** (0019). Ancestors registered only so
  inherited grants have somewhere to hang never take the check as
  `latest_scan_id`.
- **The newer check wins** a walked place. An MG-rooted check walks every
  subscription and resource group below it, so a resource group's pointer
  may reference an MG, subscription or RG check.
- **Reading "current"**: `app/core/current_view.py`. A grant counts only if
  it comes from the check that owns its place.
- A principal keeps the newest non-null name any check resolved;
  `last_activity_at` only moves forward (written by the coverage pass).
- Deleting a check `SET NULL`s pointers but keeps the rows.
  `POST /settings/registry/rebuild` re-derives both registries.

`app/core/changes.py` compares each completed check with the previous one
of the same place and writes one `access_changes` row per difference:
grants, PIM eligibilities (only when both checks read PIM), and group
members (only for groups both checks read). `source` says how exact the
time and author are; a `daily_check` row becomes `activity_log` once the
matching event is pulled. A group member's row becomes `entra_audit` once
the Entra ID audit log has its entry: same member, same group or a group
nested inside it in both checks (named in `direct_group_name`), between the
two checks.
A whole group nested into another stays `daily_check`: its entry names the
nested group, not the members it brings. `subject_key` is the grant id for grants and
eligibilities, `member:<group>:<member>` for memberships. The feed is
filled once from the checks on file when it is empty (at startup).

## Activity Log

```
activity_pulls       id, subscription_id (a GUID, "management-groups" or "entra-directory"),
                     start_time, end_time, status, started_at, finished_at, error,
                     events_seen, events_persisted, progress_done, progress_message,
                     heartbeat_at, origin, schedule_id, created_at
activity_events      id, subscription_id, event_data_id ("daily:<hash>" on a daily row),
                     caller, caller_type, caller_oid (JWT object id, 0022),
                     operation_name, resource_id, resource_type, status,
                     correlation_id, occurred_at (daily row: the last),
                     raw JSON (slimmed; NULL on a daily row),
                     event_count (default 1), first_occurred_at (daily rows)
                     UNIQUE (subscription_id, event_data_id);
                     IX (subscription_id, occurred_at); IX (caller_oid, occurred_at);
                     IX (occurred_at); IX (status)
activity_compacted_days
                     PK (subscription_id, day), compacted_at, events_before, rows_after
operation_names      operation PK (lowercased), provider (IX), display_name,
                     resource_type_display, fetched_at
```

- **Slim payloads (0021 → 0022):** `raw` keeps what the event drawer shows
  (`authorization`, `properties`, `http_request`, `description`, …) and a
  claims subset (`oid`, `appid`, `idtyp`, `upn`, `name`, `ipaddr`); columns
  already stored and the rest of the caller's JWT are dropped
  (`app/core/activity_payload.py`). `caller_oid` is filled on insert.
- **Daily counts (0038)**, `app/core/activity_compaction.py`: once a day is
  old enough not to be collected again, the retention run folds it. Kept in
  full: events by a person, changes to access or governance
  (`Microsoft.Authorization` / `Microsoft.Management` writes and deletes,
  elevated access) and failures. The rest becomes one row per caller,
  operation, resource and outcome for the day, with `event_count`.
  "Started" rows whose outcome is stored are dropped. Readers sum
  `event_count`; a later collection skips what was folded on such a day.
- **Operation names**: Azure's display names from provider operations
  metadata (`app/core/operation_names.py`), refreshed after a pull for
  providers missing or older than 7 days.

## Entra ID audit log (0040, 0041)

```
directory_audit_events
                     id, audit_id (Entra's id, UNIQUE),
                     kind (group | role | eligible | activation, 0041),
                     action (add | remove), occurred_at (IX), result,
                     group_id (NULL for a role entry), group_name, role_id (0041),
                     member_id (IX), member_type, member_name,
                     initiator_type (user | app), initiator_id, initiator_name,
                     raw JSON, created_at
```

Only group membership entries and Entra role entries (given, removed,
eligible through PIM, activated; the list is `ACTIVITIES`), read through
Microsoft Graph by an `activity_pulls` row with
`subscription_id = "entra-directory"` (`app/tools/entra_audit.py`). Ids
are lowercased, as in `group_memberships`. Never part of the Activity Log:
no usage findings, not on the Activity page. Whether the log can be read is
in `app_settings` (`entra_audit`: `status`, `checked_at`, `message`).

## Reviews and follow-up

```
responsibilities     scope_id PK → scopes (CASCADE), person_id (IX), person_name,
                     person_email, source (manual | tag), set_by, set_by_name, set_at
responsible_suggestions
                     scope_id PK → scopes (CASCADE), tag_value, person_id, person_name,
                     person_email, status (open | unmatched | ignored | used), checked_at
review_rounds        id, scope_id → scopes (CASCADE; NULL = the admins' round),
                     period (YYYY-MM, container local time), reviewer_id (IX),
                     reviewer_name, opened_at, due_at, finished_at, finished_by_name
                     UNIQUE (scope_id, period); UNIQUE (period) WHERE scope_id IS NULL
review_items         id, round_id → review_rounds (CASCADE), grant_fingerprint (IX),
                     scope, scope_name, scope_level, principal_*, role_definition_id,
                     role_name, level, azure_id, moved_from_scope,
                     decision (keep | remove | change | ask | NULL),
                     suggested, suggested_rule, suggested_title (0030), note,
                     decided_by, decided_by_name, decided_at, carried_from_round_id,
                     ask_remind_at, gone_at, added_at
                     UNIQUE (round_id, grant_fingerprint)
todos                id, round_id → review_rounds (CASCADE), item_id → review_items
                     (CASCADE, unique), grant_fingerprint, action (remove | change),
                     scope, scope_name, principal_*, role_name, note, advice_rule,
                     how JSON, commands JSON, reviewer_id, reviewer_name,
                     decided_by_name, created_at, due_at,
                     status (open | done | dismissed), done_at,
                     dismissed_reason, dismissed_by_name
assessments          id, scope_id (NULL = the whole tenant), scope_name, created_at,
                     created_by, created_by_name, access_as_of, note,
                     statuses JSON, numbers JSON
notifications        id, kind (ask | review_opened | review_due | reminder |
                     todo_overdue | data_stale), recipient_id (IX), recipient_name,
                     subject_ref, payload JSON, dedupe_key UNIQUE, created_at (IX),
                     sent_at, channel, error
ai_texts             hash PK, model, prompt_version, text, created_at
```

- **Responsible people** (`app/core/responsible.py`): set by an admin, they
  apply to everything inside a place unless someone is set lower down. Not
  the Azure Owner role. Each check reads the tag named in `app_settings`
  key `responsible` (`{"tag_name": "owner"}` by default) on subscriptions
  and resource groups: `open` when it matches exactly one Entra ID user,
  `unmatched` otherwise. A `used` / `ignored` decision sticks while the tag
  value is unchanged.
- **Monthly reviews** (`app/core/reviews.py`, `open_and_sync`): after every
  check, at startup and on `POST /reviews/open`. One round per place with a
  responsible person, plus the admins' round. A grant belongs to the
  nearest responsible person at or above the place it was given on; one
  held by the reviewer moves up (`moved_from_scope`). Items copy the grant.
  A grant kept last month with no newer `access_changes` entry starts as
  `keep`. While undecided, `suggested*` holds the playbook's advice
  (`app/core/advice.py`).
- **To-dos** (`app/core/todos.py`): finishing a round turns each `remove`
  or `change` decision on a grant still there into a to-do, with the
  advice's steps and commands at that moment. The daily check sets `done`
  once the grant is gone.
- **Assessments**: a saved report's control statuses and key numbers,
  read-only, so later reports can say what changed.
- **Notifications** (`app/core/notify.py`): the outbox. `dedupe_key` makes
  every writer idempotent. Nothing is sent while `NOTIFICATION_CHANNEL` is
  empty; otherwise waiting rows go out every minute. Rows closed without
  sending have `sent_at` and `error`; a failed send keeps `sent_at` empty
  and is retried after a pause. `data_stale` goes to the admins at most
  once a day per set of problems (`app/core/staleness.py`).
- **AI texts** (`app/core/ai_wording.py`): cached wording, keyed by a hash
  of the model, prompt version and facts sent.

## Settings, jobs and audit

```
azure_credentials    id PK (always 1), tenant_id, client_id,
                     client_secret_encrypted BYTEA (Fernet), updated_at
auth_settings        id PK (always 1), enabled, tenant_id, client_id,
                     allowed_group_ids, admin_group_ids (comma-separated), updated_at
app_settings         key PK, value JSON, updated_at
job_schedules        id, kind (snapshot | activity_pull), target, every_hours (1–720),
                     at_hour (0–23, daily only), enabled, initial_window_days,
                     next_run_at, last_run_at, last_job_id, last_error, failures,
                     settled_job_id, covered_until, paused_at, paused_by, created_at
                     UNIQUE (kind, target); IX (enabled, next_run_at)
audit_events         id, occurred_at (IX), actor (IX), actor_name, method, path,
                     status_code, client_ip (0034)
```

- `app_settings` keys: `default_schedules` (whether the default schedules
  were created, and which an admin deleted on purpose), `responsible` (the
  tag name), `advice` (thresholds and break-glass identities), `ai` (on/off,
  model, the API key encrypted with `ENCRYPTION_KEY`).
- `job_schedules.last_job_id` is not a foreign key: it points into `scans`
  or `activity_pulls` depending on `kind`. Snapshot targets are normalised
  ARM paths and pull targets lowercased GUIDs (or `management-groups`), so
  the unique index catches duplicates. Since 0036 a schedule records how its
  last job ended (`settle` in `app/core/scheduler.py`); `covered_until` is
  where a pull schedule's next run starts.
- `azure_credentials` and `auth_settings` are singletons (row 1); the
  resolvers handle a missing row.

## Retention

`app/core/retention.py` runs `RETENTION_FIRST_RUN_MINUTES` (5) after startup,
then every `RETENTION_INTERVAL_HOURS` (24), and on **Apply now** under
Settings › Database. `0` keeps everything. These are backend settings
(`app/core/config.py`); see [deployment.md](deployment.md#backend-settings)
for how to change them.

| Setting | Default | Deletes |
|---|---|---|
| `EVENT_RETENTION_DAYS` | 120 | Activity Log events (machine activity is folded into daily rows first), and the Entra ID audit log's membership entries |
| `SNAPSHOT_KEEP_PER_SCOPE` | 6 | completed checks beyond the newest N per root scope |
| `FAILED_SNAPSHOT_RETENTION_DAYS` | 7 | failed checks |
| `AUDIT_RETENTION_DAYS` | 365 | audit rows |
| `CHANGE_RETENTION_DAYS` | 365 | changes feed entries |
| `PULL_RECORD_RETENTION_DAYS` | 30 | records of finished pulls |

A check any `scopes.latest_scan_id` points to is never deleted, nor one in
flight; *Delete old checks* (Settings › Database) follows the same rules. Deletes run in batches.
Freed space is reused by Postgres; `VACUUM FULL` returns it to the disk.

## Migrations

Migrations live in [`backend/alembic/versions/`](../backend/alembic/versions/),
numbered with four digits. The backend's `entrypoint.sh` waits for the
database and runs `alembic upgrade head` before uvicorn, so a container
always starts on the current schema.

| #     | What |
|-------|------|
| 0001  | `scans` + `role_assignments` |
| 0002  | `role_assignments.scope_name` |
| 0003  | `scans.progress_total / progress_done / progress_message` |
| 0004  | `scans.hierarchy` |
| 0005  | rename `scans.mgmt_group` → `scope`, add `scope_level` |
| 0006  | lowercase ARM scopes, recompute `scope_level`, lowercase the hierarchy |
| 0007  | `azure_credentials` |
| 0008  | `role_assignments.role_type` |
| 0009  | `auth_settings` (Entra sign-in) |
| 0010  | `auth_settings.admin_group_ids` |
| 0011  | `audit_events` |
| 0012  | `role_definitions` cache |
| 0013  | `activity_pulls` + `activity_events` |
| 0014  | `group_memberships` |
| 0015  | `scan_group_resolutions` |
| 0016  | `heartbeat_at`; one in-flight job per scope / subscription; rows in flight at migration time marked `failed` |
| 0017  | `scan_coverage`, `findings`, `review_decisions` |
| 0018  | `scopes` + `principals` registries, backfilled from completed checks |
| 0019  | `scopes.latest_scan_id` only for walked places; downgrade is a no-op |
| 0020  | `job_schedules` |
| 0021  | expression index on the JWT object id (Postgres only; replaced by 0022) |
| 0022  | `activity_events.caller_oid` + index; `raw` slimmed in place (irreversible); unused B-trees dropped |
| 0023  | `role_assignments.condition` / `condition_version`, backfilled |
| 0024  | PIM: `role_eligibilities`; `role_assignments.assignment_type` / `end_at`; `scans.eligibility_status` / `eligibility_error` |
| 0025  | grant origin on `role_assignments` (backfilled); `operation_names` |
| 0026  | `access_changes`; `job_schedules.at_hour`; `app_settings` |
| 0027  | `responsibilities`, `responsible_suggestions`; `scopes.tags` |
| 0028  | `review_rounds`, `review_items` |
| 0029  | `notifications` |
| 0030  | suggested decisions on `review_items`, the `change` decision, `todos`; kind `todo_overdue` |
| 0031  | `principal_app_owner` / `principal_sp_type` on `role_assignments`, `app_owner_org` / `sp_type` on `principals` |
| 0032  | `assessments` |
| 0033  | `ai_texts` |
| 0034  | `audit_events.client_ip` |
| 0035  | notification kind `data_stale` |
| 0036  | `job_schedules.failures` / `settled_job_id` / `covered_until` (backfilled) |
| 0037  | `job_schedules.paused_at` / `paused_by`; `origin` and `schedule_id` on `scans` and `activity_pulls` |
| 0038  | `activity_events.event_count` / `first_occurred_at`; `activity_compacted_days` (folding happens in the retention run, not the migration) |
| 0039  | index on `access_changes.activity_event_id` (deleting events no longer scans the feed) |
| 0040  | `directory_audit_events`; `access_changes.actor_type` / `direct_group_name`; source `entra_audit` |
| 0041  | `directory_audit_events.kind` / `role_id`; `group_id` optional (Entra role entries) |

### Migration conventions

- Always provide a `downgrade()`, even a no-op (`pass`) with a comment
  saying why.
- Backfill with raw `op.execute("UPDATE …")`, not ORM queries: the ORM may
  reflect a later model than the migration's.
- `make check` runs every migration from an empty Postgres and compares the
  result with the models (`backend/tests/pg`).

## Querying directly

```bash
make psql test                                        # opens psql
make psql test ARGS="-c 'SELECT count(*) FROM scans'"  # one-shot
```

```sql
-- Top 10 principals by grant count in the latest completed check
SELECT principal_name, principal_type, count(*) AS n
FROM role_assignments
WHERE scan_id = (SELECT max(id) FROM scans WHERE status = 'done')
GROUP BY 1, 2 ORDER BY n DESC LIMIT 10;

-- Activity Log events stored, counting daily rows by what they stand for
SELECT subscription_id, sum(event_count) AS events, count(*) AS rows
FROM activity_events GROUP BY 1 ORDER BY 2 DESC;

-- Disk space per table (Postgres only)
SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC;
```
