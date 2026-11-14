# Architecture

## Components

```
                    ┌──────────────────────────────────────┐
                    │  Browser (React 19 SPA)              │
                    │  test: Vite dev server :5173         │
                    │  prod: nginx :80 (serves the SPA,    │
                    │        proxies /api to the backend)  │
                    └──────────────────────────────────────┘
                                       │
                                       │ /api/* (HTTP/JSON)
                                       ▼
              ┌─────────────────────────────────────────────────┐
              │  FastAPI backend (Python 3.12, uvicorn) :8000   │
              │  ─────────────────────────────────────────────  │
              │  middleware: request guard (host names), CORS,  │
              │              audit log                          │
              │  routers:   checks + tree + diff, places,       │
              │             people & apps, grants, findings,    │
              │             changes, responsible, reviews,      │
              │             to-dos, report, AI, Activity Log,   │
              │             jobs + schedules, settings, health  │
              │  loops:     sweeper, scheduler, retention       │
              └─────────────────────────────────────────────────┘
                  │             │            │             │           │
            SQLAlchemy 2.0  Azure SDK   Azure Monitor  Microsoft    SMTP / Graph
                  │         (mgmt+auth) (Activity Log)  Graph       mail, Anthropic
                  ▼             │            │         (names,      API (optional)
        ┌──────────────────┐    ▼            ▼          groups)
        │  PostgreSQL 18   │   Azure ARM (RBAC, PIM, MG, subscriptions,
        │  :5432 (test)    │   RGs, resources, Monitor)
        └──────────────────┘
```

The stacks are not identical. Both have `db`, `backend`, `frontend` and
`mailpit` (catches every email the app sends). The `test` overlay mounts
source for hot reload and publishes every port on 127.0.0.1: UI `:5173`,
API `:8000`, Postgres `:5432`, Mailpit `:8026`. The `prod` overlay runs
built images: nginx on `:80` (127.0.0.1 unless `BIND_ADDRESS` is set),
the backend reachable only through nginx, Mailpit on `:8025`, and a
`backup` service that writes one `pg_dump` a day into `backups/`. See
[deployment.md](deployment.md).

Tables are described in [data-model.md](data-model.md); routes and
their auth levels in [api.md](api.md) and
[reference.md](reference.md#api-surface).

## Background loops

Three loops start with the backend (`app/main.py` lifespan):

- **Sweeper** (`core/sweeper.py`) marks a check or Activity Log pull
  failed when its worker goes quiet (no `heartbeat_at` update) for
  longer than `STUCK_SCAN_TIMEOUT_SECONDS`. A worker that finishes late
  cannot overwrite that.
- **Scheduler** (`core/scheduler.py`) wakes every
  `SCHEDULER_TICK_SECONDS`. At start it closes jobs the previous process
  left in flight, creates the default schedules (a daily check of the
  whole tenant at `DEFAULT_CHECK_HOUR`, an hourly Activity Log pull per
  subscription), opens the month's reviews and fills the changes feed
  if it is empty. On each tick it claims due schedules and runs them on a
  small thread pool (`SCHEDULER_MAX_CONCURRENT_JOBS`), starts the nightly
  second pass over the Activity Log, recomputes findings after new
  activity, tells admins when the data stops updating, and delivers
  waiting notifications.
- **Retention** (`core/retention.py`) runs `RETENTION_FIRST_RUN_MINUTES`
  after start, then every `RETENTION_INTERVAL_HOURS`. It prunes old
  events, snapshots, audit rows, changes and pull records, and folds old
  machine activity into daily counts (`core/activity_compaction.py`).

Times in the container follow `TZ` (UTC when unset): the daily check,
the monthly reviews and the second pass use it.

## The check pipeline

A check (called a scan in the code and the API) runs in
`app/tools/iam_access.py:run_scan`. A manual one (`POST
/api/iam-access/scans`) runs as a FastAPI `BackgroundTask`; a scheduled
one runs on the scheduler's thread pool. Both open their own DB session.

```
POST /api/iam-access/scans  →  Scan row inserted (status=pending)
or a due schedule           └─ worker starts:

   ┌────────────┐    ┌─────────────┐    ┌──────────────┐    ┌──────────────┐
   │  Phase 1   │ →  │   Phase 2   │ →  │   Phase 3    │ →  │ Derived data │
   │  Discover  │    │  List       │    │  Resolve     │    │ (never fails │
   └────────────┘    └─────────────┘    └──────────────┘    │  the check)  │
                                                            └──────────────┘
  Walk MG → subs      For each target:      Microsoft Graph     registries,
  → RGs, register     ARM list_for_scope.   getByIds for every  findings,
  resources; keep     Dedupe by ra.id,      principal; group    changes feed,
  tags and parent     classify the actual   members. Then PIM   responsible
  links in            scope; ancestor       eligibilities and   suggestions,
  scan.hierarchy.     chain for inherited.  active schedules.   reviews + to-dos
```

Progress is published to the `scans` row after every step
(`progress_done`, `progress_total`, `progress_message`, `heartbeat_at`)
so the UI shows a determinate / indeterminate bar with a live message.

- **Phase 1 (discover)**, `tools/scan_walk.py`. Lists the places to
  read and their names, so phase 2 knows `progress_total` and can fill
  `scope_name`. Resources inside each resource group are registered in
  the hierarchy (their grants come from the RG's `list_for_scope`; they
  are not separate targets). A place Azure refuses is marked unread; a
  root that is missing or unreadable fails the check
  (`ScopeUnreachable`), and so does losing the connection midway
  (`ScanInterrupted`). A half-read check is never recorded as done.
- **Phase 2 (list)**, `tools/scan_list.py`. The only phase that talks to
  the Azure Authorization API for grants. Per target: one
  `list_for_scope` call (it returns inherited grants too) → dedupe by
  `ra.id` → derive the level from the returned `scope` (Azure returns
  the defining scope for inherited rows) → a `role_assignments` row.
  Grants defined above the walked tree trigger `ensure_ancestor_chain`.
- **Phase 3 (resolve)**, `tools/graph_resolver.py` and
  `tools/graph_groups.py`. Batches every distinct `principal_id` by type
  into `getByIds` calls, and reads the members of groups that hold
  access. A refusal (401 / 403: a missing Graph permission) loses only
  that type and is tolerated. Graph not answering at all (network,
  throttling, server error) fails the check: without names every
  identity would read as "no longer exists".
- **PIM** (`tools/pim.py`) adds eligible assignments
  (`role_eligibilities`) and marks time-bound active grants. A PIM
  failure is recorded on the check (`eligibility_status`) and never
  fails it.

Results and the terminal status land in one commit: either the whole
result set lands as `done`, or nothing does. Then the derived data is
built, each step logged and skipped on failure: the `scopes` /
`principals` registries (`POST /api/settings/registry/rebuild`
recovers them), findings, the changes feed (compared with the previous
check of the same place), the default schedules, suggestions for
responsible people from the `owner` tag, and the monthly reviews, which
also close to-dos whose grant is gone.

## Scope levels and inheritance

Azure RBAC scopes form a strict top-down hierarchy:

```
mgmt_group       /providers/Microsoft.Management/managementGroups/<id>
   └── mgmt_group   ← MGs can nest
   └── subscription /subscriptions/<id>
         └── resource_group /subscriptions/<id>/resourceGroups/<rg>
               └── resource /subscriptions/<id>/resourceGroups/<rg>/providers/<rp>/<type>/<name>
```

Inheritance flows downward only: an assignment at a parent scope
applies at every descendant. Conversely a child scope can never grant
access at a parent. The walker checks this invariant when it adds a
node (`add_hierarchy_node` in [`backend/app/tools/scan_walk.py`](../backend/app/tools/scan_walk.py)
logs an error if a child's level is shallower than its parent's, using
`LEVEL_ORDER` from [`backend/app/tools/scope_ids.py`](../backend/app/tools/scope_ids.py)).

The walker dispatches on the check's `scope_level`:

| Check target     | Walks to                                                   |
|------------------|------------------------------------------------------------|
| `mgmt_group`     | child mgmt groups + subscriptions → RGs → their resources  |
| `subscription`   | RGs in this subscription → their resources                 |
| `resource_group` | the RG itself → its resources                              |
| `resource`       | just the resource itself                                   |

For `subscription` / `resource_group` / `resource` checks, anything
above the root that comes back via `list_for_scope` inheritance
gets re-attributed onto the root node by the `/tree` endpoint and
labelled `inherited from <scope>`, so ancestor nodes don't leak in.

## Casing of ARM IDs

ARM IDs are technically case-insensitive but Azure returns inconsistent
casings: the canonical `/managementGroups/` for direct assignments,
fully-lowercased `/managementgroups/` for many inherited rows. We
normalize all scope strings to lowercase at every ingestion point
(`normalize_scope` in [`tools/scope_ids.py`](../backend/app/tools/scope_ids.py)).
Migration `0006_normalize_scope_casing.py` lowercased the legacy data
and recomputed `role_assignments.scope_level` so old checks render
correctly.

## Tree response — re-rooting and synthesizing

The `/api/iam-access/scans/{id}/tree` endpoint
(`backend/app/api/scan_results.py`) produces a tree rooted at the
check's `scope`. Two transformations happen there:

1. **Re-rooting**: ancestor scopes captured during the walk (e.g. a
   tenant root reached by `ensure_ancestor_chain`) are *not* rendered
   as their own top-level nodes. Their grants are re-attributed onto the
   root with the original `scope` / `scope_name` preserved, so the
   frontend renders them as inherited from that scope.
2. **Resource synthesis**: a grant whose scope is below the root but not
   in the hierarchy (sub-resources under a storage account, for example)
   gets a synthetic node anchored under its nearest in-tree ancestor.

## Credential resolution

`backend/app/core/azure.py:get_credential()` chooses an Azure
credential in this order:

1. `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` env vars
   (`ClientSecretCredential`), from the env file (`.env.test` /
   `.env.prod`) of a read-only service principal; `make whoami <env>`
   shows which identity it is.
2. Encrypted `azure_credentials` row entered through the Settings page
   (decrypted with `ENCRYPTION_KEY`).
3. `DefaultAzureCredential` (developer `az login`, managed identity, etc.).

The result is `lru_cache`d. The credentials PUT / DELETE handlers call
`get_credential.cache_clear()` so a freshly saved credential takes
effect on the next check. RBAC Lens never writes to Azure: to-dos and
review exports produce commands for a person to read and run.
