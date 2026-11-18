# Development

## Prerequisites

- Docker + Docker Compose (the stack runs entirely in containers), `make`,
  `openssl`.
- A read-only Azure SP (`sp-rbaclens-reader`, see
  [deployment.md](deployment.md#azure-identity-least-privilege)) with
  `Reader` on the management group you want to scan and the Entra role
  Directory Readers for principal-name resolution.
  Its credentials go in the env file: see
  [Azure credentials](#azure-credentials).

## Everything goes through make

One entry point, the `Makefile` at the repo root, on the Makefile recipe
([README-MAKE.md](README-MAKE.md)): `make <command> <test|prod>`, and
`make` alone lists the commands. The environment word is required for
every stack command (`make up` without it refuses), so nothing reaches
prod by accident.

```bash
make up test                     # build if needed, start, health check
make logs test SERVICE=backend   # tail one service (omit SERVICE for all)
make status test                 # containers
make health test                 # containers, GET /api/health, the URL to open
make shell test                  # shell in the backend (SERVICE=frontend|db)
make psql test                   # psql on rbaclens_test; ARGS="-c 'select 1'" for one-shot
make migrate test                # alembic upgrade head (up also does it on start)
make down test                   # stop, volumes kept
make restart test                # down + up: recreates containers so env changes apply
make check                       # lint, pytest (SQLite, then Postgres), tsc, vitest
make lint                        # Ruff rules + format check, ESLint
```

### First run

`make up test`:

1. creates `.env.test` from `.env.test.example` if it's missing;
2. generates `ENCRYPTION_KEY` (a Fernet key) when it's empty — it
   encrypts credentials saved on the Settings page, so keep it;
3. builds and starts the stack, then runs `make health test`. Without
   Azure credentials it says so and starts anyway: pages work, checks
   need credentials.

That brings up:

- `rbaclens-db-test` — PostgreSQL 18 on `:5432`.
- `rbaclens-backend-test` — FastAPI on `:8000`, hot reload via uvicorn
  `--reload` and a source-mounted volume.
- `rbaclens-frontend-test` — Vite dev server on `:5173`, HMR via the same
  source mount.

The backend container's `entrypoint.sh` runs `alembic upgrade head`
before starting uvicorn, so a fresh start always lands on a migrated
schema.

### Env files

One per environment: `.env.test` and `.env.prod`, git-ignored, created
from the committed `.env.test.example` / `.env.prod.example`. Docker only
injects the environment when a container is *created*: after editing an
env file, `make env-check test` shows which values the running backend
doesn't have yet, and `make restart test` applies them. `make whoami
test` tells you which Azure identity the env file gives the stack.

## Azure credentials

The stack signs in to Azure with a service principal's three values, set
per environment in its env file:

```bash
# .env.test (and .env.prod)
ARM_CLIENT_ID=<app (client) id>
ARM_CLIENT_SECRET=<secret value>
ARM_TENANT_ID=<tenant id>
```

Then `make restart test` (or `prod`) and `make whoami test` to see which
identity is set (the first characters of the ids only). Use a read-only
identity ([deployment.md](deployment.md#azure-identity-least-privilege)).

Left empty, the backend uses the credentials saved on **Settings →
Credentials** instead (encrypted with `ENCRYPTION_KEY`). The env files
are git-ignored; keep them that way, and keep the secret in a password
manager too, since `make purge` or a lost machine takes the file with it.

## Tests

```bash
make check                       # everything: lint, pytest twice, tsc, vitest
make check ARGS="-k pim -x"      # extra pytest args (the SQLite run)
```

`make check` runs in throwaway containers built from the test images
(`docker compose run --rm`), so it works whether the stack is up or not.
On GitHub, `.github/workflows/ci.yml` runs the same `make check` on every
push to a branch and on every pull request (the **Actions** tab). It runs:

1. **Lint:** Ruff with the rule set in `backend/pyproject.toml` and its
   format check (`ruff format` fixes it); ESLint with TypeScript's, React
   hooks' and accessibility (jsx-a11y) rules (`frontend/eslint.config.js`).
2. **pytest on SQLite:** the `app_with_sqlite` fixture in
   [`backend/tests/conftest.py`](../backend/tests/conftest.py) boots the app
   on an in-memory SQLite, with real Azure / Microsoft Graph calls
   monkey-patched out.
3. **pytest on Postgres** ([`backend/tests/pg`](../backend/tests/pg)), on a
   throwaway Postgres (`docker/docker-compose.check.yml`, data in memory):
   every migration from an empty database, the migrated schema compared
   with the models (a model changed without a migration fails here), then
   the app's tests again on Postgres. A new test module that boots the app
   goes in `tests/pg/test_smoke.py`. Frontend checks are the
TypeScript build (`tsc -b --noEmit`) and Vitest (pure helpers: scope and
principal utilities, findings evidence, jobs, the ⌘K palette, the snapshot
picker, PIM wording).

For a debugger or a watcher, run inside the running stack:

```bash
make shell test                          # then: pytest -x --pdb
make shell test SERVICE=frontend         # then: npm run test:watch
```

## Lockfiles

Builds install exact versions: the backend from `backend/requirements.lock`
(`requirements-dev.lock` for the test image) with `pip --require-hashes`,
the frontend from `frontend/package-lock.json` with `npm ci`. A package
that isn't listed, or whose hash differs, fails the build.

After changing a dependency in `backend/pyproject.toml` or
`frontend/package.json`, or to take newer versions within the ranges:

```bash
make lock         # regenerate the three lockfiles (uv pip compile, npm)
make check
make build prod   # then make up prod
```

Commit the lockfiles with the change.

On GitHub, Dependabot (`.github/dependabot.yml`) opens weekly pull requests
on `dev` for the frontend's npm packages, the Docker base images and the
GitHub Actions; CI runs `make check` on each. The backend's Python packages
are left to `make lock`: Dependabot can't regenerate hash-checked lockfiles.

## Database

### Migrations

Alembic lives at [`backend/alembic/`](../backend/alembic/); the full
version list is in [data-model.md](data-model.md#migrations). To add one:

```bash
make shell test                          # then:
alembic revision --autogenerate -m "describe the change"
```

Review the generated file under
[`backend/alembic/versions/`](../backend/alembic/versions/) (autogenerate
catches column changes well, but always read the diff), then
`make migrate test`.

### Backup, restore, reset

```bash
make backup test                                  # pg_dump -Fc → backups/test-<timestamp>.dump, safe while running
make restore test BACKUP=backups/test-<…>.dump    # replaces the database — asks you to type 'test'
make purge test                                   # deletes the database volume — asks for 'yes'
make up test                                      # fresh database, migrated on start
```

`backups/` is git-ignored. `restore` stops the backend and frontend, then in
one transaction empties the database and loads the dump, so tables a newer
migration added don't survive a restore of an older backup. If the dump
can't be read to the end, nothing changes. The backend migrates the restored
data on start.

## Releases

Work happens on `dev`; `main` only moves at a release. Write the notes
under the unversioned `## YYYY-MM-DD` heading at the top of
[CHANGELOG.md](CHANGELOG.md), then:

```bash
make release-dry V=1.0.0         # shows the planned changes, touches nothing
make release V=1.0.0             # asks, then: changelog heading, version bump, commit, main fast-forward, tag v1.0.0
./scripts/release.sh 1.0.0 --no-push   # the same, kept local; prints the push command for later
```

The version lives in `backend/app/version.py` (reported by
`GET /api/health`), `backend/pyproject.toml` and `frontend/package.json`.
The release also copies the changelog to `frontend/public/CHANGELOG.md`,
which **Settings → Changelog** displays (the frontend image is built from
`frontend/` only); don't edit that copy by hand.
With an `origin` remote it pushes `dev`, `main` and the tag in one atomic
push, which the pre-push check below can refuse: then nothing is sent and
the release stays local. Without a remote the push command is printed for
later.

### Publishing the images

The pushed `vX.Y.Z` tag starts `.github/workflows/release.yml` on GitHub.
It checks that the tag, `backend/app/version.py` and the changelog agree,
runs `make check` on the tagged commit, builds both images (target `prod`), scans them with the Trivy image pinned
in the Makefile and stops on a fixable CRITICAL vulnerability, then pushes
them to Docker Hub for linux/amd64 and linux/arm64 (tags `X.Y.Z` and
`latest`, with an SBOM and provenance), publishes each Docker Hub
repository's overview from [dockerhub.md](dockerhub.md) with a short
description, and creates the GitHub release from the changelog section. Nothing is published if a step fails; the
run is under the repository's **Actions** tab.

Once, before the first release:

1. On Docker Hub, create the public repositories `rbaclens-backend` and
   `rbaclens-frontend`, and a personal access token with **Read, Write &
   Delete** (Account settings → Personal access tokens; Docker Hub's
   overview API needs Delete), never your password.
2. On GitHub, under the repository's **Settings → Secrets and variables →
   Actions**, add the secrets `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`.
   Images go to the `gclntm` account unless the variable
   `DOCKERHUB_NAMESPACE` names another.

The overview is `docs/dockerhub.md` (full URLs only, Docker Hub doesn't
resolve relative links; 25 kB at most). `.github/workflows/dockerhub-overview.yml`
publishes it whenever it changes on `main`, after each release, or by hand
(Actions → Docker Hub overview → Run workflow); the short descriptions are
in that file.

Actions in the workflow are pinned to a commit, with the release in a
comment; update them together (Dependabot can).

### Branch and tag rules

GitHub enforces these on every push, yours included, so a slip can't
rewrite what is published. Set them under the repository's **Settings →
Rules → Rulesets**, each with **Enforcement status: Active**. Rulesets are
free on public repositories; a private one needs a paid plan, so add them
when the repository goes public.

| Ruleset | Targets | Rules | Bypass |
|---------|---------|-------|--------|
| **History** (New branch ruleset) | branches `main`, `dev` | Restrict deletions · Block force pushes | nobody |
| **Releases** (New tag ruleset) | tags `v*` | Restrict updates · Restrict deletions · Block force pushes | nobody |
| **Pull requests** (New branch ruleset, once others contribute) | branches `main`, `dev` | Require a pull request before merging · Require status checks to pass: `check` (from CI) | Repository admin, Always |

- **History** keeps `make release` working: it pushes `dev`, `main` and the
  tag as fast-forwards, which these rules allow.
- **Releases** means a published `vX.Y.Z` always names the code its Docker
  Hub images were built from; a wrong release is fixed with a new version,
  never by moving the tag.
- **Pull requests** makes everyone else go through a pull request that
  passed CI. Don't add it without the admin bypass: `make release` pushes
  directly, and a required check can't have run on a commit not yet pushed.

While there, under **Settings → Advanced Security** (formerly Code security; public repository):
turn on Dependabot alerts, secret scanning with push protection, and
CodeQL's default setup.

### Plans

A plan for a feature or a release is a working document: save it as
`docs/plan-<topic>.md`. Git ignores that name (`.gitignore`), so plans stay
on this machine and are never pushed. What ships is described in the
CHANGELOG and the docs above.

## Keeping private data out of the repository

The repository is meant to be published; the data it is developed against
is not. Real names and ids stay in the env files and the database,
never in tracked files: tests and docs use invented ones (`mg-contoso`,
`grp-platform-admins`, `admins@rbaclens.test`).

A check enforces it, with the private names kept in the git directory,
which is never committed or pushed:

| File | Holds |
|------|-------|
| `.git/info/denylist` | names you add by hand: organisation, hosts, people |
| `.git/info/denylist-db` | names and ids from a database: `make denylist prod` |
| `.git/info/allowlist` | generic names from those lists that may appear (`mg-prod`) |

```bash
make hooks            # once per clone: install the pre-push check
make denylist prod    # refresh the names from the database, now and then
make public-check     # check the tracked files any time
```

The pre-push hook (`scripts/git-hooks/pre-push`, `scripts/public_check.py`)
refuses a push when a commit to send adds a private name or has one in
its message, when an author or committer email is not a GitHub no-reply
address, or when it carries the history from before publication (kept on
this machine under the `pre-public` tag). It also runs `gitleaks` when it
is installed (`brew install gitleaks`).

## Project structure

```
backend/
├── app/
│   ├── api/             # FastAPI routers, one per area (scopes, principals, grants,
│   │                    #   findings, reviews, todos, report, jobs, settings, auth…)
│   ├── core/            # Rules and services: config, auth, crypto, scheduler, jobs,
│   │                    #   findings, capabilities, conditions, advice, report, retention…
│   ├── db/              # SQLAlchemy base, session, types; models/ per area
│   ├── schemas/         # Pydantic schemas, one per area
│   └── tools/           # Azure and Graph collection: scan walk/persist, Activity Log,
│                        #   PIM, group resolution, grant origin
├── alembic/             # Migrations
├── scripts/             # Maintenance scripts
├── tests/               # pytest suite + conftest
└── pyproject.toml

frontend/
├── src/
│   ├── pages/           # Routed pages, one folder per area (scopes, principals, grants,
│   │                    #   findings, reviews, todos, report, jobs, settings…)
│   ├── components/      # Shared UI: PageTemplate, Button, Drawer, Toast, SideMenu,
│   │                    #   classes.ts (shared class strings), chips.ts, charts/
│   ├── hooks/           # useDialogFocus, useMe, useActivePolling…
│   ├── auth/            # Entra ID sign-in (AuthProvider)
│   ├── api/client.ts    # Typed REST client
│   ├── styles/          # index.css (Tailwind setup), elements.css (variables per
│   │                    #   theme, element defaults), tokens.css (Untitled UI tokens)
│   ├── theme/           # Light/dark/system theme context
│   └── ui/untitled/     # Untitled UI React components, copied as source (MIT)
└── vite.config.ts
```

The interface is styled with Tailwind utilities only, on Untitled UI's tokens;
there are no per-page CSS files. Read [design.md](design.md)
before changing the UI.

## Code style

- Backend: 4-space Python, type hints everywhere, `from __future__ import
  annotations` at the top of modules with forward refs.
- Frontend: 2-space TS, React 19 hooks. Functional components only.
- No comments unless the *why* is non-obvious.
- Avoid premature abstractions — three repeats are fine, second time
  refactor if the shape is clearer.

## Adding a new feature

A typical end-to-end change touches:

1. `backend/app/db/models/<area>.py` — new column / table (export a new table in `models/__init__.py`).
2. `backend/alembic/versions/000N_*.py` — migration.
3. `backend/app/schemas/*.py` — Pydantic types.
4. `backend/app/api/*.py` — routes.
5. `backend/tests/test_*.py` — at least the API-contract test.
6. `frontend/src/api/client.ts` — typed client method.
7. `frontend/src/pages/*.tsx` — UI.
8. `docs/data-model.md`, `docs/api.md`, `docs/iam-access.md` —
   docs (this folder).
