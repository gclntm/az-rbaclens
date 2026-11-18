# Troubleshooting

Things that have actually broken in this codebase + how to fix them.

## Backend won't start: `(sqlite3.OperationalError) no such table: scans` in tests

Cause: `Base.metadata.create_all(engine)` ran before `app.db.models`
was imported, so SQLAlchemy's metadata was empty when the schema was
created.

Fix: ensure [`backend/tests/conftest.py`](../backend/tests/conftest.py)
contains `import app.db.models  # noqa: F401` *before* the `create_all`
call. This is already in the conftest. If you fork it and remove the
import, expect this to come back.

## Settings page: "ENCRYPTION_KEY is not configured"

Symptom: saving a client secret returns 503; the Settings page banner
warns about encryption being unavailable.

Cause: the backend has no `ENCRYPTION_KEY`. `make up <env>` generates one
in `.env.<env>` when it is empty, so this happens when the env file was
edited by hand or the container predates the key.

Fix: put a key in `.env.<env>` (`make genkey` prints one), then
`make restart <env>`. See
[Deployment](deployment.md#generating-encryption_key); never change a key
that already encrypts saved secrets without rotating them.

## Checks fail with `SSL: CERTIFICATE_VERIFY_FAILED`

Symptom: checks fail with `ScanInterrupted: …` or `GraphUnavailable: …`,
and the backend log shows `CERTIFICATE_VERIFY_FAILED` for
`management.azure.com` or `graph.microsoft.com`. A check never carries on
without names or members when Azure or Graph doesn't answer: it fails and
saves nothing.

Cause: outbound TLS is intercepted by a proxy whose root CA isn't in
Python's `certifi` bundle.

Fix: bind-mount the proxy's PEM root CA into the backend container and set
`REQUESTS_CA_BUNDLE` to the in-container path
([Deployment → Corporate TLS / proxy CA](deployment.md#corporate-tls--proxy-ca)).
The Azure SDK and the Graph resolver both honour it (`SSL_CERT_FILE` as a
fallback).

## Tree shows mg-platform 'inheriting' from a subscription / resource

Cause: a stale scan was created before migration 0006 normalized the
scope casing, so `role_assignments.scope_level` was wrong on some
rows (Azure returned the same MG path in two casings; the old
case-sensitive `_level_from_scope` mis-classified the lowercased one
as `resource`).

Fix: run a fresh scan. Migration 0006 already lowercased existing
data and recomputed `scope_level`, so any scan run *after* the
migration is correct. If you still see oddities on a post-0006 scan,
file a bug.

## REINDEX returns 500 with `can only reindex the currently open database`

Cause: an earlier version of [`backend/app/api/database.py`](../backend/app/api/database.py)
ran `REINDEX DATABASE CONCURRENTLY current_database`. Postgres parses
that as a literal database name (not a function call), and the literal
"current_database" doesn't match the actual database (`rbaclens_prod` /
`rbaclens_test`).

Fix in repo: the endpoint now iterates `pg_tables` and runs
`REINDEX TABLE CONCURRENTLY "<name>"` per table. That has the same
effect, needs no super-user, and dodges the system-catalog edge cases
of `REINDEX DATABASE`.

## `Graph getByIds(<type>) failed: 403`

Symptom: backend logs show `Graph getByIds(group) failed` and the UI
shows GUIDs instead of names for groups (or apps, or users).

Cause: the Azure identity can't read that type in Microsoft Graph.
`getByIds` is gated by *type*, so a missing permission loses one type only.
A refusal doesn't fail the check; the names stay empty.

Fix: give the identity the Entra role **Directory Readers** (what
`sp-rbaclens-reader` has, see
[deployment.md](deployment.md#azure-identity-least-privilege)), or the
Graph application permission `Directory.Read.All` with admin consent. The
next check resolves the names again.

## Changes page: "who changed the group is not known here"

Symptom: someone joined or left a group that holds access, and the Changes
page only says when the daily check first saw it.

Cause, by what Data & schedules says under **Activity Log coverage →
Entra ID audit log**:

- **Not read: … AuditLog.Read.All**: the identity can't read the Entra ID
  audit log. Fix: add that Microsoft Graph application permission to
  `sp-rbaclens-reader`, grant admin consent
  ([deployment.md](deployment.md#azure-identity-least-privilege)), then
  **Check now**. The hourly collection starts by itself.
- **Not read: … licence**: Microsoft Graph refused the audit log because
  of the tenant's licence (the error code mentions a non-premium tenant).
  Reading it through the API may need Entra ID P1 or P2.
- **Collected every hour**, but this change still has no author: Entra had
  no entry for it in the window. A whole group nested into the group that
  holds access brings its members with one entry that names the nested
  group, not them; those stay "first seen". So does a change older than
  what Entra keeps (7 or 30 days) when the collection started.

## Entra ID is missing from Places, or shows as not read

Entra roles are read only by a check of the **Tenant Root Group** (the daily
check): a check of another management group or a subscription leaves the
Entra ID place out. Run the daily check, or check the Tenant Root Group once.

*Not read* means Microsoft Graph refused the role assignments: give the
identity the Entra role **Directory Readers**
([deployment.md](deployment.md#setting-up-the-service-identity)). Its grants
are then unknown, not absent: the changes feed reports nothing as removed
there until a check reads it again.

## A place shows as not read, or a check fails at its root

A place Azure refuses (403) below the root is skipped and flagged `unread`
(`unread_scopes` on the check): its grants are unknown, and the previous
check that read it stays its owner. A root that can't be read fails the
check with `ScopeUnreachable: …`; losing Azure or Graph midway fails it
with `ScanInterrupted: …` or `GraphUnavailable: …`, and nothing is saved.
See [iam-access.md](iam-access.md#checks).

Fix: give the identity **Reader** at the refused place (or **Management
Group Reader** for the tree above), or fix the network, then check again.

## No "What changed since #N" button on a place's Changes tab

Cause: the button compares a check with the previous completed check
of the same root. If the only earlier check failed, or was rooted
elsewhere, there is nothing to compare with.

Fix: check the same place again; the new check sees the prior one once
both are `done`.

## A check got stuck in `running`

Cause: the backend process died mid-check, so the worker that owns the
`running` → `done` / `failed` transition is gone. (An exception *inside*
the worker is not a cause: it rolls back and records `failed` in a fresh
transaction, see `app/core/jobs.py`.)

What covers it automatically:

- **At start**, checks and collections the previous process left in
  flight are closed as "Interrupted: the app restarted…" and their
  schedules run again on the first tick (`RECOVER_JOBS_AT_START`, default
  on).
- **The sweeper** runs every `STUCK_SCAN_SWEEP_SECONDS` (60) and marks any
  `pending` / `running` check or collection whose `heartbeat_at`
  (else `started_at`, then `created_at`) is older than
  `STUCK_SCAN_TIMEOUT_SECONDS` (3600) as `failed`. A long check that is
  still progressing is never swept.
- While the row is stuck, `POST /iam-access/scans` for the same scope
  returns 409: that is the one-in-flight guard, not a bug.
- "Latest check" queries only consider `done` rows, so a stuck row never
  passes for current data.

If you need it gone immediately, or the sweeper is disabled
(`STUCK_SCAN_SWEEP_SECONDS=0`), edit the row directly or delete it.

```bash
make psql test ARGS="-c \"UPDATE scans SET status='failed', error='killed' WHERE id=<N>\""
```

Or simpler:

```bash
make psql test ARGS="-c 'DELETE FROM scans WHERE id=<N>'"   # cascades
```

## All checks are gone after `make purge <env>`

`purge` deletes the database volume (`db_test_data` / `db_prod_data`);
it asks for `yes` and says so. `make down <env>` and `make clean <env>`
never touch volumes. Take `make backup <env>` before a purge;
`make restore <env> BACKUP=…` brings the data back.

## My `.env.test` / `.env.prod` change isn't taking effect

Docker injects the environment only when a container is *created*.
`make env-check <env>` lists which env-file values the running backend
doesn't have; `make restart <env>` recreates the containers so they do.
A variable the compose files don't list under `backend.environment` never
reaches the backend, and `env-check` keeps showing it as `DIFFERS`: add it
to the overlay ([Deployment → Backend settings](deployment.md#backend-settings)).
