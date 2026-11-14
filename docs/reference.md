# RBAC Lens

Web UI for periodic Azure RBAC access review. The backend talks to Azure
directly through the SDK and Microsoft Graph. Python
FastAPI backend, React frontend, PostgreSQL, Docker for `test` / `prod`.
Current release: see `backend/app/version.py` (0.32.0 at the time of writing).

UI follows [design.md](design.md). Operator and developer
documentation lives in [`docs/`](README.md). This file is the
living index: what exists, where it is, what changed. Update it every
time a feature, route, table, env var or stage changes, and append a line
to the [Changelog](#changelog). Don't duplicate `docs/` here; link to it.

---

## Contents

- [Stack](#stack)
- [Repository layout](#repository-layout)
- [Features](#features)
- [API surface](#api-surface)
- [Database](#database)
- [Running it — the Makefile](#running-it--the-makefile)
- [Environment variables](#environment-variables)
- [Tests](#tests)
- [Roadmap](#roadmap)
- [Conventions](#conventions)
- [Changelog](#changelog)

---

## Stack

| Layer      | Choice |
|------------|--------|
| Backend    | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · `azure-mgmt-*` + `azure-identity` · Microsoft Graph over `httpx` · `anthropic` (optional AI wording) |
| Frontend   | React 19 · TypeScript · Vite 7 · Node 24 LTS · react-router 7 · Tailwind CSS 4 · Untitled UI React (MIT components, vendored in `frontend/src/ui/untitled`) · React Aria Components · `@untitledui/icons` · recharts · MSAL (optional sign-in, loaded only when sign-in is on) |
| Database   | PostgreSQL 18 · JSONB for hierarchy / raw payloads |
| Azure auth | Read-only service principal (e.g. `sp-rbaclens-reader`) — `ARM_*` in the env file, or an encrypted row saved from Settings |
| UI auth    | Optional Entra ID sign-in, group-gated admin; signed-in non-admins see only their places and their own access — see [docs/auth.md](auth.md) |
| Email      | Mailpit catches every message in both stacks (`NOTIFICATION_CHANNEL=smtp`); real email through Graph `Mail.Send` is opt-in |
| Packaging  | Docker multi-stage · docker-compose `test` / `prod` · one entry point: the [`Makefile`](../Makefile) (`make <command> <test\|prod>`) |
| Logging    | Structured JSON; mutating API calls recorded in `audit_events` (with the caller's IP) |

---

## Repository layout

```
.
├── docs/                 reference (this), design, architecture, development, deployment, api, data-model,
│                         iam-access, auth, troubleshooting, README-MAKE, CHANGELOG,
│                         plan-* (plans of record and shipped plans)
├── backend/
│   ├── app/api/          health, auth, iam_access, scan_results, scan_export,
│   │                     role_definitions, findings, registry, scope_views,
│   │                     scope_capabilities, principal_views, grants, changes,
│   │                     responsible, reviews, todos, report, ai, advice, search,
│   │                     activity, jobs, dashboard, scope_browser, settings,
│   │                     database, audit, notifications
│   ├── app/core/         config, azure, auth, security, visibility, request_guard,
│   │                     audit, crypto, logging, jobs, scheduler, default_schedules,
│   │                     sweeper, retention, findings, role_risk, role_coverage,
│   │                     scan_coverage, coverage, capabilities, conditions,
│   │                     registry, current_view, changes, responsible, reviews,
│   │                     todos, advice, advice_texts, ai_wording, report,
│   │                     report_controls, notify, notify_email, staleness,
│   │                     activity_payload, activity_compaction, …
│   ├── app/db/           models/ (activity, checks, findings, registry, reviews,
│   │                     settings), session, base, types
│   ├── app/schemas/      pydantic models per router
│   ├── app/tools/        iam_access (run_scan), scan_walk, scan_list, scan_persist,
│   │                     scope_ids, pim, grant_origin, activity, mgmt_group_log,
│   │                     graph_resolver, graph_groups
│   ├── alembic/versions/ 0001 … 0039
│   ├── tests/            pytest, SQLite-backed, SDK calls patched; tests/pg on Postgres
│   └── entrypoint.sh     waits for DB, alembic upgrade head, uvicorn
├── frontend/src/
│   ├── pages/            overview/ (Home, MyHome for reviewers), scopes/ (Places
│   │                     index, place page, AssignmentDrawer), principals/ (People & apps index,
│   │                     person page), grants/, findings/ (Needs a look),
│   │                     changes/, reviews/, todos/, report/, jobs/ (Data &
│   │                     schedules), events/ (Activity Log), scan/ +
│   │                     IamAccessPage (old snapshot URLs, redirects),
│   │                     IamAccessDiffPage, RolesPage (Role guide),
│   │                     SettingsPage (+ settings/*), NotFoundPage
│   ├── components/       SideMenu, PageTemplate, CommandPalette (⌘K), drawers
│   │                     (role, group members), advice/, charts/,
│   │                     tree/, ConfirmDialog, Toast, classes.ts (shared class strings)
│   ├── ui/untitled/      vendored Untitled UI React components (MIT; LICENSE, UPSTREAM)
│   ├── theme/            light / dark / system theme switch
│   ├── hooks/, auth/     polling, current user, AI status; AuthProvider (MSAL)
│   ├── api/client.ts     typed API client with bearer injection
│   └── styles/           index.css (layer order), tokens.css, elements.css
├── .github/workflows/    ci.yml (make check on every push and PR), release.yml (on a vX.Y.Z tag: scan, push images to Docker Hub, GitHub release), dockerhub-overview.yml (Docker Hub page from dockerhub.md)
├── compose.yaml          the published images on their own (curl + docker compose up)
├── docker/               docker-compose.yml (base) + .test.yml, .prod.yml, .check.yml
├── scripts/              release.sh, backup-loop.sh (prod's daily backup),
│                         public_check.py + git-hooks/ (leak check)
├── Makefile              the entry point: make <command> <test|prod>  (make help)
└── .env.<env>.example    env templates; make up creates .env.test / .env.prod
```

---

## Features

UI labels in quotes are what the side menu and page headers say.

| Area | UI route | Backend | Docs |
|------|----------|---------|------|
| "Home": status tiles, what needs a look, latest changes (chart); reviewers get their own Home (their reviews, their places) | `/` | `GET /api/dashboard/overview`, `GET /api/me` | [api.md](api.md) |
| Monthly "Reviews": Keep / Remove / Change / Ask per grant, suggested decision from the advice, finish, CSV export | `/reviews`, `/reviews/:id` | `/api/reviews*` (`POST /api/reviews/open`: admin) | api.md |
| "To do": changes to make in Azure after a review, closed by the next checks; CSV and a shell script to read, then run | `/todos?status=&show=&round=&view=` | `/api/todos*` | api.md |
| "Needs a look": findings worklist, tenant-wide, filters, bulk decisions | `/findings?status=&severity=&category=&q=` | `GET /api/findings`, `/api/iam-access/findings/*` | api.md |
| "Changes": who got or lost access, when and by whom; changes per week | `/changes` | `GET /api/changes`, `GET /api/freshness` | api.md |
| "Places": tree + place page (Who can do what · Needs a look · Changes · Activity · All grants) | `/places`, `/places/<ARM path>?tab=&q=` | `/api/scopes/*` (tree, detail, assignments, findings, history, activity, capabilities) | api.md, [iam-access.md](iam-access.md) |
| "People & apps": list + person page (What they can do · Groups / Members · What they did · Needs a look), "What to do" advice | `/people?q=&type=&level=`, `/people/:id?tab=` | `GET /api/principals`, `GET /api/principals/{id}`, `GET /api/principals/{id}/activity` | api.md |
| Grant page: who, what, where, since when, given by whom, used or not, advice | `/grants/:grantId` | `GET /api/grants/{id}` | api.md |
| Responsible people per place, suggestions from the `owner` tag | Settings → "Who is responsible" | `/api/responsible/*` | api.md |
| Advice: one action per grant from a fixed playbook; optional AI wording (Claude) | on grant, person and review pages; Settings → "Advice" | `/api/ai/*`, `/api/settings/advice`, `/api/settings/ai` (admin) | api.md |
| "Report": assessment mapped to ISO/IEC 27001:2022, print to PDF, CSV annex, saved assessments to compare | `/report?scope=&compare=` | `/api/report*` (saving: admin) | api.md |
| Notifications: outbox, sent through Mailpit (smtp) or Graph email | Settings → "Notifications" | `/api/notifications*` (admin) | [deployment.md](deployment.md) |
| ⌘K search: places, people & apps, roles | ⌘K / Ctrl K / `/`, side menu button | `GET /api/search` | api.md |
| "Role guide": role catalogue, risk, usage | `/roles` | `/api/iam-access/role-definitions*` | iam-access.md |
| What changed between two checks | `/iam-access/diff/:a/:b` | `GET /api/iam-access/scans/{a}/diff/{b}` | iam-access.md |
| Old snapshot URLs | `/iam-access`, `/iam-access/:id` (redirect to Data & schedules or the place page) | `/api/iam-access/scans*` | iam-access.md |
| "Activity Log": events (browse, export) | `/events` (linked from Data & schedules) | `/api/activity/events*` | iam-access.md |
| "Data & schedules": schedules (daily check, hourly Activity Log collection), run now, recent runs, Activity Log coverage | `/admin/data` (`/jobs` redirects) | `/api/jobs`, `/api/schedules*`, `/api/activity/coverage`, `/api/activity/pulls`, `/api/iam-access/scans` | api.md |
| "Settings": Who is responsible · Advice · Notifications · Credentials · Authentication · Audit log · Database · Changelog | `/settings/*` | `/api/settings/*`, `/api/auth/*`, `/api/audit/*`, `/api/notifications*` | [auth.md](auth.md), deployment.md |
| Registry rebuild (admin, no UI) | — | `POST /api/settings/registry/rebuild` | api.md |

Old addresses `/scopes/*` and `/principals/*` redirect to `/places/*` and
`/people/*`. RBAC Lens never changes Azure: to-dos and reviews produce
commands for a person to run. `apply_tags` and `create_sp` (the other bash
scripts) are not wrapped; see Roadmap.

---

## API surface

Full reference with shapes: [docs/api.md](api.md). Routers and
their auth level (from `backend/app/main.py`):

| Prefix | Auth | Purpose |
|--------|------|---------|
| `/api/health`, `/api/auth/config` | public | liveness + version, MSAL bootstrap |
| `/api/iam-access/*` | authenticated | checks (scans), role assignments, tree, diff, group members, role definitions + usage, per-check findings + review decisions, export |
| `/api/findings` | authenticated | current findings, tenant-wide |
| `/api/scopes/*`, `/api/principals*`, `/api/grants/*`, `/api/search` | authenticated | places, people & apps, one grant, ⌘K search |
| `/api/changes`, `/api/freshness` | authenticated | changes feed, how fresh the data is |
| `/api/responsible/*`, `/api/me` | authenticated (writes: admin) | responsible people, suggestions; who the caller is |
| `/api/reviews*`, `/api/todos*` | authenticated (`POST /reviews/open`: admin) | monthly reviews; to-dos, CSV, script |
| `/api/report*` | authenticated (`POST /report/assessments`: admin) | assessment report, CSV annex, saved assessments |
| `/api/ai/*` | authenticated | AI wording: status, preview, explain |
| `/api/activity/*` | authenticated | pulls, events, export, coverage |
| `/api/jobs`, `/api/schedules*` | authenticated | recent jobs; schedules, run now |
| `/api/dashboard/overview` | authenticated | Home |
| `/api/scope-browser/*` | authenticated | subscription list (cached), refresh |
| `/api/settings/*` (credentials, auth, database, registry, advice, ai), `/api/audit/*`, `/api/notifications*` | admin | credentials, sign-in, DB maintenance, registry rebuild, advice + AI settings, audit log, notifications |

"Authenticated" and "admin" are no-ops until Entra sign-in is enabled in
Settings, so the sign-in settings can be set up.
Once on, a signed-in non-admin also passes the visibility guard
(`core/visibility.py`, deny by default). Every request goes through
`core/request_guard.py`, which refuses host names other than localhost,
`*.local`, IP addresses and `EXTRA_SERVER_NAMES`.

---

## Database

Schema reference: [docs/data-model.md](data-model.md).
Migrations `0001`–`0039` in `backend/alembic/versions/`; the backend
container runs `alembic upgrade head` on start.

Tables: `scans`, `role_assignments`, `role_eligibilities`,
`role_definitions`, `group_memberships`, `scan_group_resolutions`,
`scan_coverage`, `scopes`, `principals`, `findings`, `review_decisions`,
`access_changes`, `operation_names`, `responsibilities`,
`responsible_suggestions`, `review_rounds`, `review_items`, `todos`,
`assessments`, `ai_texts`, `notifications`, `job_schedules`,
`activity_pulls`, `activity_events`, `activity_compacted_days`,
`azure_credentials`, `auth_settings`, `app_settings`, `audit_events`.

Conventions: every schema change is an Alembic migration; JSONB via
`JSON().with_variant(JSONB(), "postgresql")` so tests run on SQLite;
times are UTC through one column type (`app/db/types.py`); scopes
lowercased at ingest. `make check` runs every migration from empty on
Postgres and compares the result with the models.

---

## Running it — the Makefile

Everything goes through the `Makefile` at the repo root:
`make <command> <test|prod>` (`make` alone = help), on the Makefile recipe in
[docs/README-MAKE.md](README-MAKE.md). The environment word is
required for every stack command; nothing defaults to prod.

| Env | Compose overlay | Mode |
|--------|--------------|------|
| `test` | `docker/docker-compose.test.yml` | hot reload, source mounts; UI `:5173`, API `:8000`, Postgres `:5432`, Mailpit `:8026` (all 127.0.0.1) |
| `prod` | `docker/docker-compose.prod.yml` | built images, nginx on `:80` (127.0.0.1 unless `BIND_ADDRESS`), API not published, Mailpit `:8025`, daily `backup` service |

```bash
make up test                  # creates .env.test + ENCRYPTION_KEY, then health
make logs test SERVICE=backend
make backup test              # pg_dump -Fc → backups/test-<timestamp>.dump
make whoami test              # which Azure identity the env file gives the stack
make check                    # lint, pytest (SQLite, then Postgres), tsc, vitest in throwaway containers
make release-dry V=1.0.0      # preview a release; make release V=1.0.0 cuts it
```

Details: [docs/development.md](development.md),
[docs/deployment.md](deployment.md).

---

## Environment variables

Defaults are from `backend/app/core/config.py`; where the compose files
set a different value, that is noted.

| Var | Required | Default | Notes |
|-----|----------|---------|-------|
| `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` | one of env **or** saved credentials | — | `sp-rbaclens-reader`; env wins over the DB row |
| `DATABASE_URL` | yes | `postgresql+psycopg://rbaclens:rbaclens@db:5432/rbaclens_test` | compose builds it from `POSTGRES_*` |
| `ENCRYPTION_KEY` | for saving credentials or an AI key via UI | — | Fernet key, generated by `make up`; see [troubleshooting.md](troubleshooting.md) |
| `STAGE` | no | `test` | `test` \| `prod` (set by the overlays) |
| `LOG_LEVEL` | no | `INFO` | test overlay: `DEBUG` |
| `CORS_ORIGINS` | no | `["http://localhost:5173"]` | JSON array |
| `EXTRA_SERVER_NAMES` | no | empty | host names besides localhost, `*.local`, IPs (backend and prod nginx) |
| `TZ` | no | UTC | container time zone: daily check, monthly reviews, second pass, backups |
| `REQUESTS_CA_BUNDLE` | no | — | CA bundle for outbound TLS behind an intercepting proxy |
| `STUCK_SCAN_TIMEOUT_SECONDS` / `STUCK_SCAN_SWEEP_SECONDS` | no | `3600` / `60` | sweeper that fails abandoned jobs |
| `SCHEDULER_TICK_SECONDS` / `SCHEDULER_MAX_CONCURRENT_JOBS` | no | `60` / `2` | job scheduler; tick `0` disables it |
| `RECOVER_JOBS_AT_START` | no | `true` | close jobs left in flight and run their schedules again; `false` with several replicas |
| `DEFAULT_SCHEDULES` / `DEFAULT_CHECK_HOUR` | no | `true` / `6` | daily check of the whole tenant + hourly Activity Log pulls, created once |
| `SECOND_PASS_HOURS` / `SECOND_PASS_HOUR` | no | `48` / `3` | nightly re-read of the Activity Log for late events; `0` turns it off |
| `EVENT_RETENTION_DAYS` / `SNAPSHOT_KEEP_PER_SCOPE` / `FAILED_SNAPSHOT_RETENTION_DAYS` / `AUDIT_RETENTION_DAYS` | no | `120` / `6` / `7` / `365` | retention policy; `0` keeps everything |
| `CHANGE_RETENTION_DAYS` / `PULL_RECORD_RETENTION_DAYS` | no | `365` / `30` | changes feed; finished pull records |
| `RETENTION_INTERVAL_HOURS` / `RETENTION_FIRST_RUN_MINUTES` | no | `24` / `5` | retention loop; interval `0` disables it (Settings → Database can still apply it) |
| `NOTIFICATION_CHANNEL` | no | empty (stacks: `smtp`) | empty = outbox only; `smtp` (Mailpit) or `email` (Graph) |
| `SMTP_HOST` / `SMTP_PORT` | for `smtp` | empty / `25` (stacks: `mailpit` / `1025`) | |
| `NOTIFICATION_SENDER` / `NOTIFICATION_ADMIN_EMAIL` | for `email` | empty | sender mailbox needs Graph `Mail.Send`; where admins' messages go |
| `PUBLIC_URL` / `NOTIFICATION_INBOX_URL` | no | empty (stacks set them) | links in messages; Mailpit inbox shown in Settings |
| `NOTIFICATION_MAX_AGE_DAYS` | no | `7` | older waiting messages are closed unsent |
| `AI_DAILY_LIMIT` | no | `200` | new AI texts per day; `0` = no limit |
| `ANTHROPIC_API_KEY` | no | — | fallback when no key is saved in Settings → Advice; the compose files don't pass it |

Compose-only: `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
(password generated by `make up`), `BIND_ADDRESS` (prod),
`BACKUP_HOUR` / `BACKUP_KEEP` (prod, `2` / `14`), `FORWARDED_ALLOW_IPS`
(prod overlay). Frontend: `VITE_API_URL` (dev proxy target). Env files:
`.env.test` / `.env.prod`, created by `make up <env>` from the committed
`.example` templates.

---

## Tests

```bash
make check                              # lint, pytest (SQLite, then tests/pg on a throwaway Postgres), tsc, vitest
make check ARGS="-k pim -x"             # extra pytest args
make lint                               # Ruff (rules + format check), ESLint (hooks, a11y); part of check
```

Backend suites (`backend/tests/test_*.py`): health, auth,
auth_settings_protected, token_check, visibility, request_guard, settings,
database, audit, logging, sweeper, job_lifecycle, job_errors, jobs,
iam_access, scan_root, graph_strict, scope_browser, role_definitions,
role_risk, role_coverage, coverage, capabilities, scope_capabilities,
conditions, pim, pim_views, grant_origin, grants, operation_names,
findings, registry, scope_views, principals, changes, responsible,
reviews, todos, advice, ai_wording, report, notifications, notify_email,
staleness, activity, activity_payload, activity_compaction,
mgmt_group_log, retention, csv_safe, rotate_encryption_key. On Postgres
(`tests/pg`): migrations, smoke. Frontend: vitest files next to the code
(`*.test.ts` / `*.test.tsx`).

---

## Roadmap

Plans of record:

- [docs/plan-untitled-ui.md](plan-untitled-ui.md) — the
  interface on Untitled UI React with light / dark / system themes.
  Complete, shipped in 0.29.0–0.31.0.
- [docs/plan-before-github.md](plan-before-github.md) — phases
  1–4 shipped (by 0.25.0). **Phase 5, publish:** cleaned and licensed
  (Apache 2.0); next, a README for strangers and the GitHub side.

Shipped earlier: [redesign-plan.md](redesign-plan.md) (phases
1–5), [plan-who-can-do-what.md](plan-who-can-do-what.md)
(phases 6–10), [plan-advice-and-report.md](plan-advice-and-report.md)
(phases 11–14).

Parked: write operations (tagging, creating identities) — RBAC Lens stays
read-only.

---

## Conventions

- `docs/` is the reference; this file is the index. A new feature needs:
  route(s), schema, migration, tests, a `docs/` section, a row in
  [Features](#features), and a line in [`docs/CHANGELOG.md`](CHANGELOG.md)
  under the unversioned date heading at the top.
- Branches: work on `dev`; `main` only moves at a release
  (`make release V=X.Y.Z`: fast-forward + tag `vX.Y.Z`).
- Operator commands go through the Makefile; docs quote `make …`
  verbatim. A new target needs a `##` description (that's the help).
- No secrets in the repo. Credentials live in the git-ignored env files
  or the encrypted `azure_credentials` row. `.claude/settings.local.json`
  is gitignored.
- UI styling: Tailwind utilities on Untitled UI's tokens
  (`styles/tokens.css`) and the shared class strings in
  `components/classes.ts`; no other CSS apart from `elements.css` and the
  report's `@page` rule. No hardcoded colours, spacing, radii — see
  [design.md](design.md).
- Keep files under ~500 lines. Known offenders: `frontend/src/api/client.ts`
  (1.9 k); just over the line: `core/findings.py`, `core/scheduler.py`,
  `pages/RolesPage.tsx`.

---

## Changelog

Release notes live in [`docs/CHANGELOG.md`](CHANGELOG.md): add
each change under the unversioned `## YYYY-MM-DD` heading at the top;
`make release V=X.Y.Z` versions it and refreshes `frontend/public/CHANGELOG.md`,
the copy Settings → Changelog shows (never edit it by hand).
