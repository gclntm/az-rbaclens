# REST API reference

Base URL: `http://localhost:8000/api` on the test stack, `http://localhost/api`
on the prod stack (the frontend's nginx forwards `/api/` to the backend).

This page is a short reference: what each route is for, who may call it,
and the rules that are not obvious from the shapes. For every field, use
FastAPI's generated pages: `http://localhost:8000/docs` (Swagger),
`/redoc` and `/openapi.json`. They sit at the backend's root, not under
`/api`, so only the test stack serves them (its backend port is published);
the prod nginx forwards `/api/` only.

All responses are JSON unless noted (CSV and script downloads). Errors use
FastAPI's shape: `{ "detail": "<message>" }`.

## Who may call what

`backend/app/main.py` mounts every router under `/api` at one of three levels:

| Level | Routers | Rule |
|---|---|---|
| Public | `health`, `GET /auth/config` | No token, ever: the SPA reads the sign-in config before it can sign in. |
| Signed in | everything not listed elsewhere | `require_auth` + the visibility guard (below). No-op while sign-in is off. |
| Admin | `/settings/*` (auth, credentials, database, registry, advice, ai), `/audit/*`, `/notifications*` | `require_admin`: the token's `groups` claim holds one of the admin group ids. |

Some signed-in routers also check `require_admin` on single calls:
`POST /reviews/open`, `POST /report/assessments`, and every change under
`/responsible`. While sign-in is off, every caller counts as an admin.
`/settings/auth` is open while sign-in is off, so it can be set up; locked
out, `make auth-off <env>` turns sign-in off from the host
([auth.md](auth.md#locked-out-turn-sign-in-off-from-the-host)).

**People who are not admins.** With sign-in on, `app/core/visibility.py`
refuses (`403`) every call not on its allow-list, so a new endpoint is
closed to them until it is added there. Allowed, checked or filtered
(see [auth.md](auth.md#what-people-who-are-not-admins-see)):

- `GET /me`; `/freshness` with the times only (no last-check error, no warnings);
- place pages (`/scopes/{id}`, `/scopes/assignments|capabilities|findings|history|activity?scope=`, `/responsible/of`): their places only;
- `/scopes/tree` and `/responsible/tree`: their places, plus the places above for context;
- `/principals` and `/principals/{id}`: identities whose access applies to their places (filtered to those grants), and their own; `/principals/{id}/activity`: their own only;
- `/grants/{id}`: grants that apply to their places, and their own;
- `/changes`, `/findings`, `/search`, `/todos` (and `/summary`, `/export`, `/script`), `/report` (and `/annex`, `/assessments`): filtered; a `scope` parameter must be one of their places;
- `/iam-access/role-definitions` and `/{id}`: built-in roles, and a custom role only when a grant they see uses it; no `/usage`;
- `/reviews` and the round calls (read, decide, accept advice, finish, export): rounds they review or that cover their places;
- `POST /todos/{id}/dismiss` on their to-dos; `/ai/status|preview|explain` (the endpoint checks the grant or identity).

**Two refusals on every call** (`app/core/request_guard.py`, see
[deployment.md](deployment.md#security-headers-and-host-names)):

- `400` when the `Host` header is not a name the app answers to
  (`localhost`, `*.local`, an IP address, or a name in `EXTRA_SERVER_NAMES`);
- `403` on `POST` / `PUT` / `PATCH` / `DELETE` from another website: its
  `Origin` is neither the address it was sent to nor in `CORS_ORIGINS`.
  Calls without an `Origin` (curl, scripts) pass.

Every `POST` / `PUT` / `PATCH` / `DELETE` is written to the audit log
([auth.md](auth.md#audit-log)).

## Health and sign-in

| Call | What it does |
|---|---|
| `GET /health` | `{status, version, stage, db}`. `status` is `ok`, or `degraded` when `SELECT 1` fails (`db: "unreachable"`). `version` is `app/version.py`. |
| `GET /auth/config` | Public: `enabled`, tenant id, client id, for MSAL. |
| `GET` / `PUT /settings/auth` | Sign-in settings: `enabled`, `tenant_id`, `client_id`, `allowed_group_ids`, `admin_group_ids`. All ids must be GUIDs; turning sign-in on needs a tenant, a client and at least one admin group. |
| `GET /me` | `{signed_in, oid, name, is_admin, responsible_for: [{scope_id, name, level}]}`. Sign-in off: `signed_in: false`, `is_admin: true`, no places. |

## Checks (snapshots)

A check is a `scans` row. Most start from schedules (see *Jobs and
schedules*); one-off checks use this router.

| Call | What it does |
|---|---|
| `POST /iam-access/scans` `{"scope": "mg-platform"}` | Start a check; returns the row in `pending` (201). `scope` is a management group name, a subscription GUID, `<sub-guid>/<rg>`, or any ARM path down to a resource. 400 for bad input; **409** when a check of the same scope is already pending or running. |
| `GET /iam-access/scans?limit=50` | Recent checks, newest first (`limit` 1–200). |
| `GET /iam-access/scans/{id}` | One check, with live `progress_*` while running and `unread_scopes` (places Azure refused). |
| `DELETE /iam-access/scans/{id}?force=false` | Deletes the check and its rows (cascade). **409** while running, and, unless `force=true`, while it still owns a place (`scopes.latest_scan_id`). |
| `GET /iam-access/scans/{id}/role-assignments` | Every grant in the check; filters `principal_type`, `role_name`, `scope_level`. |
| `GET /iam-access/scans/{id}/tree` | The check's hierarchy with grants per node. Ancestors only seen through inheritance hang on the root node. |
| `GET /iam-access/scans/{old}/diff/{new}` | `added`, `removed`, `kept_count`. A grant is `(scope, role_definition_id, principal_id)`, so a re-created Azure assignment counts as kept. 400 when the roots differ. |
| `GET /iam-access/scans/{id}/export?fmt=csv\|json` | Download, with the same filters as role-assignments (the file name gains `-filtered-`). |
| `GET /iam-access/scans/{id}/groups/{group_id}/members` | Transitive members the check read. 404 when the check could not read the group; 200 with `[]` for an empty group. |

## Findings

Findings are the reasons a grant needs a look. They are computed at the
end of every check, again when an Activity Log pull brings new events for
a subscription it covers, and on demand (`app/core/findings.py`).

| Category | Meaning |
|---|---|
| `high_risk_broad_scope` | Owner / Contributor / UAA-class role at management group or subscription |
| `unresolved_principal` | Graph could not resolve the principal (likely deleted) |
| `unused` | none of the role's action patterns seen in the window, with events on file for the subscription |
| `over_broad` | partly used; a narrower built-in covers every operation seen |
| `custom_replaceable` | custom role whose use a built-in covers |
| `redundant` | the same principal holds a broader-or-equal role above |
| `membership_unknown` | group whose transitive members could not be read |
| `drift_added` | not in the previous completed check of the same root |
| `foreign_group` | a group of another tenant (a partner, through delegated admin access; principal type `ForeignGroup`) holds a high-risk role, Azure or Entra. Its name and members can't be read, so it never gets `unresolved_principal` or usage findings |
| `entra_standing_admin` | a person holds, not through PIM, an Entra role that can change who has access (break-glass identities left out) |
| `entra_app_privileged` | an app holds an Entra role that can change who has access or get keys and passwords |
| `entra_too_many_global_admins` | more than four identities hold Global Administrator, standing or eligible |
| `entra_role_via_group` | such an Entra role comes through a group |

Entra roles (the place `/providers/microsoft.aadiam`) get only the four
`entra_*` categories and `foreign_group`: no usage is recorded for them (`app/core/entra_findings.py`).

A finding's **fingerprint** is `sha256(scope | role GUID | principal |
category)[:40]`: stable across checks, so a decision carries over.

| Call | What it does |
|---|---|
| `GET /findings?status=&severity=&category=&q=&limit=100&offset=0&per=finding` | Every finding on the current grants, worst first; a grant seen by several checks appears once. `status` is the effective one, or `decided`. `per=grant` pages by grant. `summary` always covers everything. |
| `GET /iam-access/scans/{id}/findings?severity=&category=&status=` | One check's findings with `summary`. |
| `POST /iam-access/scans/{id}/findings/recompute?window_days=90` | Rebuild coverage and findings now. 400 unless the check is `done`. |
| `PUT /iam-access/findings/{fingerprint}/decision` | `{decision: reviewed\|accepted_risk\|snoozed\|resolved, note, snoozed_until}`; `snoozed` needs a date. Upsert; the actor comes from the token. |
| `POST /iam-access/findings/decisions` | The same verdict on up to 1,000 fingerprints in one transaction; `{updated: n}`. |
| `DELETE /iam-access/findings/{fingerprint}/decision` | 204; 404 when nothing was recorded. |

A `snoozed` decision past its date reads as `open`.

## Places, people and grants

**The current grant set.** Checks overlap, so every count on these pages
uses one rule (`app/core/current_view.py`): a grant is current when it
comes from the check that currently owns its place, the `latest_scan_id`
of its nearest registered place. Places no check walked (the Tenant Root
Group seen only through inheritance) take their grants from the newest
completed check that recorded them.

| Call | What it does |
|---|---|
| `GET /scopes/tree?root=&include_resources=false` | Nested places with `latest_scan` and `stats` (`direct_assignments`, `assignments_below`, `high_risk`, `open_findings`). |
| `GET /scopes/{scope_id}` | One place (ARM path without the leading slash, any case): `ancestors`, `children`, first and last seen. 404 when no completed check walked it. |
| `GET /scopes/assignments?scope=&source=direct\|inherited\|below` | Every current grant that applies here, with `counts` for all three sources, coverage, findings, `activated` / `until`, conditions and origin; `eligible` lists PIM eligibilities. |
| `GET /scopes/findings?scope=&status=&severity=` | Findings on those grants, with `summary`. |
| `GET /scopes/history?scope=&limit=50` | Checks rooted here or above, newest first; `is_current`, `previous_done_id` for a diff. |
| `GET /scopes/capabilities?scope=&inside_limit=30` | *Who can do what*: each identity once, at its strongest level (below), with its grants, eligibilities, `keys_for`, `member_count`; `inside` lists child places that give someone more; `pim` summarises what PIM found. |
| `GET /scopes/activity?scope=&most_active_limit=10` | Activity Log per subscription under the place, the owning check's coverage (attributed and unattributed callers), and the most active grants. |
| `GET /principals?q=&principal_type=&limit=100` | Identity registry search, with `assignment_count`, `group_count`, `last_activity_at`. |
| `GET /principals/{id}?members_limit=200` | The identity page: current grants (direct and through groups, Entra roles included), `eligible`, `groups`, `members` (groups), findings, `summary`, and `entra_activations` (Entra roles it activated through PIM, from the Entra ID audit log: `at`, `role_id`, `role_name`). 404 when no check has seen it. |
| `GET /principals/{id}/activity?days=90&recent_limit=25` | Its own Activity Log events, matched on `caller_oid` (a group: its members'). |
| `GET /grants/{grant_id}` | One current grant in plain words: who, role, level and `powers`, where, origin, PIM, condition, coverage, findings, advice, and `review` when this month's round covers it. 404 when the latest check does not have it. |
| `GET /search?q=&limit=8` | ⌘K: places, identities and roles, exact → prefix → substring. Under 2 characters: empty. |
| `POST /settings/registry/rebuild` (admin) | Re-derive the place and identity registries from every completed check. Idempotent. |

`grant_id` is `grant_fingerprint(scope, role, principal)`: the finding
fingerprint without the category. Every grant row elsewhere carries it.

**Levels** (`app/core/capabilities.py`, from each role's `actions −
notActions` and `dataActions − notDataActions`):

| Level | Means | Examples |
|---|---|---|
| `access` | can change who has access (`roleAssignments/write`) | Owner, User Access Administrator |
| `keys` | can fetch keys, credentials or secrets | Contributor, Storage Account Contributor, Key Vault Secrets User |
| `change` | can create, reconfigure or delete resources | Network Contributor |
| `data` | works on contents; `data_write` when it can change them | Storage Blob Data Reader / Owner |
| `view` | reads configuration only | Reader, AcrPull |
| `unknown` | role definition never read; never treated as `view` | |

**Origin** on every grant row: `since` (Azure's `createdOn`; a PIM
eligibility's start), `given_by_id`, `given_by_name`, `given_by_type`. Null
when Azure recorded nothing.

**PIM** (`app/tools/pim.py`): active grants carry `activated` (`Assigned`
standing or `Activated`) and `until` (null = no end). Eligibilities are
separate rows. A check never fails on PIM: `eligibility_status` is `read`,
`failed`, or null before 0024.

**Conditions** (`app/core/conditions.py`): `condition_kind` (`delegation` |
`storage` | `other` | null) and `condition_text` in plain words. A
condition only narrows a grant; it never changes the level.

## Roles

| Call | What it does |
|---|---|
| `GET /iam-access/role-definitions?q=&role_type=&risk=&unused=&sort=` | Every role definition read by a check, with `assignment_count` over the latest check per root. `sort`: `name`, `usage`, `risk`. |
| `GET /iam-access/role-definitions/{role_id}` | One definition (GUID or full ARM id): permissions, assignable scopes, `role_risk`, `risk_reasons`. 404 until a check has read it. |
| `GET /iam-access/role-definitions/{role_id}/usage` | Where it is held, deduplicated over the latest check per root. Admins only in practice (not on the guard's list). |

`role_risk` (`app/core/role_risk.py`) is `high` for `*`, `*/write|delete|action`,
`Microsoft.Authorization/*/write|delete`, or wildcard Key Vault secret
data actions; else null.

## Changes

### `GET /changes?scope=&principal=&days=30&direction=all|given|removed&powerful_only=false&limit=100&offset=0`

Who got or lost access, when, and who did it (`app/core/changes.py`),
newest first. `given` covers `granted`, `eligible_added`, `member_added`;
`removed` the opposite three. Returns `total`, `items` (one page), counts
`given` / `removed` / `powerful` before `direction` and `powerful_only`,
and `weeks[]` per ISO week. `items[].source` is `activity_log` (exact, with
author), `created_on` (Azure's or Entra's own record), `entra_audit` (someone
joining or leaving a group, or an Entra role given or removed, from the
Entra ID audit log) or `daily_check` (first seen, no author). PIM
activations are never changes. Entra role changes also come straight from
the audit log after each collection (`record_entra_audit_changes`): no
`check_id`, and never twice for what a check reports. With `entra_audit`, `actor_type` is `user` or
`app` (Entra records an app as the author: Teams or Outlook for Microsoft 365
groups, access packages, PIM, the AD sync) and `direct_group_name` names the
nested group the member was added to or removed from, when it isn't the
group that holds access.

### `GET /freshness`

`access_as_of` (newest check behind the current picture), `activity_up_to`
(newest Activity Log event), `daily_check_at_hour` with `timezone` (`TZ`,
`UTC` when unset), `next_check_at`, `last_check`. All null before the
first check.

`warnings`: what keeps the data from being kept up to date, worst first,
each `{kind, text, names, path, severity}` (`app/core/staleness.py`):
`schedules_off`, `no_check`, `check_failed`, `check_stale` (newest scheduled
check over 26 hours old), `activity_behind` (more than its interval plus 2
hours), `activity_at_risk` (under 30 days before Azure deletes uncollected
events) and `activity_lost` (more than 90 days behind). Home shows one
banner per warning.

## Overview

`GET /dashboard/overview?attention_limit=8&freshness_days=30`: Home's
figures over the current grants: `kpis` (`assignments`, `principals`,
`high_risk_broad`, `unused`, `open_findings`, `needs_look`,
`unresolved_principals`, `added_30d` / `removed_30d`, `by_level`), `access_as_of`, `activity_up_to`, `attention[]`, `top_roles[]`,
`freshness`, `jobs[]` and `findings_pending[]` (checks whose findings were
never computed).

## Responsible people

Reads for everyone signed in; changes need an admin and are audited.
Places are passed as `?scope=` (ARM path, any case).

| Call | What it does |
|---|---|
| `GET /responsible/tree` | Management groups, subscriptions and resource groups with `responsible` and where the setting comes from. |
| `GET /responsible/of?scope=` | The person for one place, and where it is set. |
| `PUT /responsible?scope=` `{"person": "email, UPN or object id"}` | Set or replace; 400 when Graph finds no such user. |
| `DELETE /responsible?scope=` | Remove the setting on that place; 404 when none. |
| `GET /responsible/suggestions` | From the Azure tag (`open`, `unmatched`, `ignored`). |
| `POST /responsible/suggestions/use` / `ignore` `{"scope": …}` | Use the suggested person (`source: tag`), or hide it while the tag is unchanged. |
| `GET` / `PUT /responsible/settings` `{"tag_name": "owner"}` | The tag read for suggestions; a change re-reads every place in the background. |

## Monthly reviews

Anyone signed in can read. The round's reviewer or an admin decides and
finishes.

| Call | What it does |
|---|---|
| `GET /reviews?mine=&period=YYYY-MM` | Rounds, newest month first, with `progress`, `blocking`, `can_decide`. |
| `GET /reviews/{id}` | The round and its items, with the advice's `suggested` decision, `moved_from_scope`, `carried`, `gone_at`, `remove_command`. |
| `PUT /reviews/{id}/items/{item_id}` `{"decision": "keep"\|"remove"\|"change"\|"ask"\|null, "note": …}` | Decide or clear. `ask` sets a reminder 7 days on; `keep` marks the grant's open findings reviewed. 403 for someone else's round, 409 once finished. |
| `POST /reviews/{id}/accept-advice` | Confirm the suggested decision on every item still to decide; counts per decision. |
| `POST /reviews/{id}/finish` | 409 while an item whose grant still exists is undecided or asked. `remove` and `change` items become to-dos. |
| `GET /reviews/{id}/export` | CSV of the decisions, with the Azure CLI command for `remove` (RBAC Lens never runs it). |
| `POST /reviews/open` (admin) | Open this month's rounds and update their items now (also runs after every check). |

## To do

Changes to make in Azure after a finished review (`app/core/todos.py`):
one per `remove` or `change` decision on a grant still there. The daily
check closes a to-do once it no longer sees the grant; one still open 14
days after the round finished is overdue.

| Call | What it does |
|---|---|
| `GET /todos?status=open\|done\|dismissed\|all&show=mine\|all&round_id=` | Open first, oldest due first. `show=mine`: the rounds you review. |
| `GET /todos/summary` | `{open, overdue, mine_open, mine_overdue}`. |
| `POST /todos/{id}/dismiss` `{"reason": …}` | Close it without the change; the reviewer or an admin. 409 without a reason or when it is not open. |
| `GET /todos/export?status=all&show=all` | CSV. |
| `GET /todos/script?show=all` | The open `remove` to-dos as one shell script to read, then run (text/plain). |

## Report

An assessment of the whole tenant or one place, mapped to ISO/IEC
27001:2022 Annex A controls (`app/core/report.py`). `scope` empty = the
whole tenant (admins only); 404 for an unknown place.

| Call | What it does |
|---|---|
| `GET /report?scope=` | `summary` sentences, `controls` with a status, privileged grants, identities to look at, least privilege, reviews, changes, `recommendations` and `numbers`. |
| `GET /report/annex?scope=` | CSV: every grant with level, how it is given, origin, advice and controls. |
| `GET /report/assessments?scope=` | Saved assessments, newest first. |
| `POST /report/assessments` (admin) `{"scope": …, "note": …}` | Save this report's statuses and numbers, read-only, as a baseline. |

## Advice and AI wording

| Call | What it does |
|---|---|
| `GET` / `PUT /settings/advice` (admin) | The playbook's `new_days` (1–365), `expiring_days` (1–180) and `break_glass` identities (exempt from the standing-admin rules). |
| `GET` / `PUT /settings/ai` (admin) | `enabled`, `model`, `api_key` (write only; stored encrypted, so it needs `ENCRYPTION_KEY`); reads `key_source`, `daily_limit`, `written_today`. 400 on a bad value. |
| `GET /ai/status` | `{available, model}`. |
| `POST /ai/preview` `{"grant_id"}` or `{"principal_id"}` | Exactly what `explain` would send. Sends nothing. |
| `POST /ai/explain` | The advice rewritten in plain words (`text`, `model`, `cached`). 409 when AI is off or over the daily limit, 502 when the call fails. |

See [deployment.md](deployment.md#ai-wording-optional).

## Jobs and schedules

Recurring checks and Activity Log pulls (`app/core/scheduler.py`). A
schedule says *when*; each run inserts an ordinary `scans` or
`activity_pulls` row. The scheduler wakes every `SCHEDULER_TICK_SECONDS`
(60) and runs jobs on `SCHEDULER_MAX_CONCURRENT_JOBS` (2) threads. A due
schedule is claimed by a conditional `UPDATE`, so it never starts twice.
A target with a job in flight is retried 5 minutes later.

- **Outcomes:** a failed job keeps `last_error` until a run succeeds;
  `failures` sets the retry (15 min, 1 h, then 4 h when sooner than the
  normal run). At start, jobs the previous process left in flight are
  closed as interrupted and run again (`RECOVER_JOBS_AT_START`).
- **Incremental pulls:** a scheduled pull covers `[max(now − 89 d,
  covered_until − 1 h), now]`; manual and failed pulls don't move
  `covered_until`. Events dedupe on `event_data_id`.
- **Second pass:** once a night from `SECOND_PASS_HOUR`, the last
  `SECOND_PASS_HOURS` of each subscription are read again for late events
  (`origin: "recheck"`).

| Call | What it does |
|---|---|
| `GET /jobs?limit=50&kind=snapshot\|activity_pull&status=` | Checks and pulls merged, newest first, with `origin`, `schedule_id`, `error_summary` (the error in one plain sentence) and `unread`. |
| `GET /activity/coverage` | Per subscription (plus `management-groups`): `state` (`ok`, `behind`, `at_risk`, `lost`, `never`, `no_schedule`), `days_left` before Azure's 90 days run out, and the second pass. `entra_audit`: whether the Entra ID audit log can be read (`status`: `readable`, `no_permission`, `licence_required`, `unavailable`, or null before it is tried), `checked_at`, `message`, and its schedule (`schedule_id`, `enabled`, `collecting`, `covered_until`). |
| `POST /activity/entra-audit/check` | Try the Entra ID audit log now and, once it is readable, add its hourly collection. Returns the same `entra_audit` object. |
| `GET /schedules` | Every schedule with `last_job_status`, `last_error_summary`, `failures`, `paused_at` / `paused_by`, `covered_until`. |
| `POST /schedules` | `{kind, target, every_hours (1–720), at_hour (0–23, daily only), enabled, initial_window_days (1–90, pulls), start_now}` → 201. Pull targets: a subscription GUID, `management-groups` or `entra-directory` (the Entra ID audit log). 409 for a duplicate `(kind, target)`. |
| `PATCH /schedules/{id}` | `enabled`, `every_hours`, `at_hour` (`-1` clears), `initial_window_days`. Switching off stamps `paused_at` / `paused_by`. |
| `DELETE /schedules/{id}` | 204; its jobs are kept. |
| `POST /schedules/{id}/run` | Start now (202) and restart the cadence; 409 when the target has a job in flight. |

## Activity Log

Pulls start from **Data & schedules** (scheduled or once); the Activity
page (`/events`) browses what was pulled.

| Call | What it does |
|---|---|
| `POST /activity/pulls` `{subscription_id, start, end}` | Background pull (201, `pending`). Window ≤ 90 days, `end` not in the future, times without a zone read as UTC. `subscription_id` is a GUID, `management-groups` (the logs of every management group the newest checks saw, `tools/mgmt_group_log.py`) or `entra-directory` (the Entra ID audit log's membership changes, `tools/entra_audit.py`). 409 when one is in flight for it. |
| `GET /activity/pulls?subscription_id=&limit=50` · `GET /activity/pulls/{id}` | Recent pulls; one pull with live progress. |
| `DELETE /activity/pulls/{id}` | Drops the record only; its events stay. |
| `GET /activity/events?subscription_id=&caller=&operation_name=&status=&from=&to=&limit=500` | Newest first (`limit` 1–5000). `caller` and `operation_name` match substrings. Rows carry `operation_display`, `event_count` and `first_occurred_at`. |
| `GET /activity/events/{id}` | One event plus `raw`, the slimmed Azure record (null on a daily row). |
| `GET /activity/events/export?fmt=csv\|json&…` | Same filters, `limit` up to 200,000 (default 50,000), streamed. Never `raw`. |

A **daily row** stands for `event_count` machine events of one caller,
operation, resource and outcome on one day, from `first_occurred_at` to
`occurred_at` (see [data-model.md](data-model.md)).

## Notifications (admin)

| Call | What it does |
|---|---|
| `GET /notifications/status` | `connected`, `channel`, `sender`, `admin_email`, `inbox_url`, counts `pending` / `sent` / `skipped`, `failed_last`, `by_kind`. |
| `GET /notifications?limit=50` | The outbox, newest first, with `payload`. |
| `POST /notifications/test` `{"to": "you@example.org"}` | Send one test message now. 409 when no channel is connected, 400 for a bad address, 502 with the error when sending fails. |

## Settings and maintenance (admin)

| Call | What it does |
|---|---|
| `GET /settings/credentials` | `tenant_id`, `client_id`, `client_secret_set`, `source` (`env` \| `db` \| `default` \| `none`), `encryption_available`. The secret is never returned. |
| `PUT /settings/credentials` | `client_secret`: null = unchanged, `""` = clear. 503 without `ENCRYPTION_KEY`. Clears the credential cache. |
| `DELETE /settings/credentials` | Drops the saved row. |
| `GET /settings/database/stats` | Database, table and index sizes (null on SQLite). |
| `POST /settings/database/vacuum[?full=true]` · `/analyze` · `/reindex` | Maintenance. `full=true` rewrites tables and locks each one meanwhile; `reindex` runs `REINDEX TABLE CONCURRENTLY` per table. |
| `GET /settings/data-kept` | What is kept: `values` that apply (saved over the server's), `sources` (`saved` \| `server`), `server`, `bounds`, `never` (values where 0 means never), `stored` per kind (`count`, `oldest`, `bytes` on Postgres), `folded_until`, `bytes_per_check`, `checks_per_day`. |
| `PUT /settings/data-kept` `{values: {name: days \| null}}` | Save values (null clears one: back to the server's). 422 on a bad value. The audit entry carries the values before and after in `detail`. |
| `POST /settings/data-kept/preview` `{values}` | What a retention run with these values would delete, per kind, and the days it would fold; nothing is saved or deleted. |
| `GET` / `POST /settings/database/retention` | The values that apply now; `POST` applies them now and returns what was deleted. |
| `POST /settings/database/cleanup-scans` `{older_than_days, only_failed}` | Deprecated (Settings › Data kept replaces it; removed in a later release). Delete finished checks older than N days, never one in flight or still owning a place. |
| `GET /audit/events?limit=100&offset=0` | The audit log, newest first (`limit` ≤ 500). `detail`, when set, says what the change changed (never the request body). |
| `GET /scope-browser/subscriptions?mgmt_group=` | Subscriptions read live from Azure (cached in the process), optionally under one management group. 502 when Azure fails. |
| `POST /scope-browser/refresh` | Clear that cache (204). |

The scope browser is mounted at the signed-in level, but the visibility
guard leaves it to admins.
