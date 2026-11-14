# RBAC Lens

Web UI + REST API for periodic Azure access reviews. It checks any RBAC
scope (management group, subscription, resource group, or single
resource) and shows every place, every person or app, and every grant:
what it allows, where, since when and who gave it. It compares each
check with the one before, so it can tell who got or lost access.

The app answers one question in plain words: who can do what, where,
and since when. It runs a daily check of the whole tenant, keeps a feed
of changes, and names a responsible person who reviews each place every
month ([`docs/plan-who-can-do-what.md`](docs/plan-who-can-do-what.md),
shipped). It also says what to do about it: advice on every grant, a
to-do list after each review, and an assessment report mapped to
ISO/IEC 27001 ([`docs/plan-advice-and-report.md`](docs/plan-advice-and-report.md), shipped).
RBAC Lens only reads Azure: it uses a read-only service principal
(`sp-rbaclens-reader`) and never changes anything there.

## Quick start

```bash
make up test          # first run creates .env.test + ENCRYPTION_KEY
open http://localhost:5173
make                  # every command; always `make <command> <test|prod>`
```

Azure credentials: put `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID`
in `.env.test` and run `make restart test`, or save them on the Settings page
after the first start.

### Just run it (published images)

No clone, no build: the images are on Docker Hub (`gclntm/rbaclens-backend`,
`gclntm/rbaclens-frontend`, linux/amd64 and linux/arm64).

```bash
mkdir rbaclens && cd rbaclens
curl -fsSLO https://raw.githubusercontent.com/gclntm/az-rbaclens/main/compose.yaml
printf 'POSTGRES_PASSWORD=%s\nENCRYPTION_KEY=%s\n' \
  "$(openssl rand -hex 24)" "$(openssl rand -base64 32 | tr '+/' '-_')" > .env
docker compose up -d          # then open http://localhost:8080
```

Then save the Azure credentials on Settings → Credentials. Options,
upgrades and backups are in the header of [`compose.yaml`](compose.yaml).

Backend is at `http://localhost:8000` (OpenAPI / Swagger at
`http://localhost:8000/docs`). Postgres is exposed on `:5432`. Every email
the app sends is caught by Mailpit: `http://localhost:8026` (prod:
`http://localhost:8025`, this machine only).

## Documentation

Everything that's worth more than a paragraph lives in [`docs/`](docs/).
Start with [`docs/README.md`](docs/README.md) for the index. The living
index of features, routes, tables and env vars is
[`docs/reference.md`](docs/reference.md); the UI rules are in
[`docs/design.md`](docs/design.md).

| Doc                                                  | What it covers                                              |
|------------------------------------------------------|-------------------------------------------------------------|
| [`docs/architecture.md`](docs/architecture.md)       | Components, background loops, check pipeline, Azure RBAC inheritance model |
| [`docs/development.md`](docs/development.md)         | Local dev with `make`, tests, migrations, backups, releases |
| [`docs/README-MAKE.md`](docs/README-MAKE.md)         | The Makefile recipe this repo follows                       |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md)             | Release notes                                               |
| [`docs/deployment.md`](docs/deployment.md)           | Production deploy, env vars, secrets, encryption key, email |
| [`docs/api.md`](docs/api.md)                         | REST API reference                                          |
| [`docs/iam-access.md`](docs/iam-access.md)           | Feature deep-dive: scopes, inheritance, tree, diff, search  |
| [`docs/auth.md`](docs/auth.md)                       | Optional Entra ID sign-in, admins, what non-admins see      |
| [`docs/data-model.md`](docs/data-model.md)           | DB schema, migrations, indexes                              |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Common errors and how to fix them                           |
| [`docs/plan-before-github.md`](docs/plan-before-github.md) | Plan of record: phases 1–4 shipped; phase 5 (publish) under way |
| [`docs/plan-untitled-ui.md`](docs/plan-untitled-ui.md) | Shipped (0.29.0–0.31.0): the interface on Untitled UI React, light and dark |
| [`docs/plan-advice-and-report.md`](docs/plan-advice-and-report.md) | Shipped: advice on every grant, to-dos after reviews, ISO 27001 assessment report, optional AI wording |
| [`docs/plan-who-can-do-what.md`](docs/plan-who-can-do-what.md) | Shipped redesign: plain words, changes feed, responsible people, monthly reviews |
| [`docs/redesign-plan.md`](docs/redesign-plan.md)     | Earlier redesign (phases 1–5, shipped): registries, findings, schedules |

## Repo layout

```
.
├── backend/                # FastAPI app (Python 3.12, SQLAlchemy 2.0)
│   ├── app/                # Source
│   ├── alembic/versions/   # DB migrations (0001…0039)
│   └── tests/              # pytest suite (SQLite; tests/pg on Postgres)
├── frontend/               # React 19 + Vite 7 + TypeScript, Tailwind CSS 4,
│   └── src/                # Untitled UI React (vendored in src/ui/untitled)
├── .github/workflows/      # ci.yml: make check on every push; release.yml: images to Docker Hub;
│                           # dockerhub-overview.yml: the Docker Hub page (docs/dockerhub.md)
├── compose.yaml            # run the published images on their own
├── docker/                 # Compose files for the make stacks (test / prod / check)
├── scripts/                # release.sh, backup-loop.sh, public_check.py
├── docs/                   # Documentation (you are reading the index)
├── Makefile                # the entry point: make <command> <test|prod>
└── .env.<env>.example      # env templates (make up creates .env.test / .env.prod)
```

## Commands

Everything goes through the `Makefile` as `make <command> <test|prod>`. The
environment word is required; nothing defaults to prod
([recipe](docs/README-MAKE.md)):

```bash
make up test | down test | restart test | logs test SERVICE=backend | health test
make shell test | psql test | migrate test
make whoami test             # which Azure identity the env file gives the stack
make backup test | restore test BACKUP=backups/<file>.dump
make check                   # lint, pytest (SQLite and Postgres), tsc, vitest; throwaway containers
make release-dry V=1.0.0     # preview a release; make release V=1.0.0 cuts it
```

See [`docs/development.md`](docs/development.md) for the full reference.

## License

[Apache License 2.0](LICENSE). Copyright 2026 gclntm; see [NOTICE](NOTICE)
for the Untitled UI components (MIT) it includes.
