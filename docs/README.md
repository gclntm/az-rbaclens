# Documentation index

Everything you need to operate, develop, and extend the RBAC Lens
web UI lives here. The living index of features, routes, tables and env
vars is [reference.md](reference.md); the UI rules are in
[design.md](design.md).

## Read me first

- [Architecture](architecture.md) — components (FastAPI / React / Postgres),
  background loops, check pipeline, Azure RBAC inheritance model.
- [Development](development.md) — local dev through the Makefile
  (`make <command> <test|prod>`), tests, migrations, backups, releases.
- [README-MAKE.md](README-MAKE.md) — the Makefile recipe this repo follows.
- [CHANGELOG.md](CHANGELOG.md) — release notes.
- [dockerhub.md](dockerhub.md) — the overview shown on the Docker Hub image pages (published when it changes on `main` and at each release).
- [Deployment](deployment.md) — production stack, env vars, secrets,
  encryption-key generation, email, backups, and setting up the read-only
  service identity.

## Reference

- [API](api.md) — REST endpoints, request / response shapes, examples.
- [Data model](data-model.md) — Postgres schema, migrations, indexes.

## Feature deep-dives

- [IAM Access](iam-access.md) — scoping, cascading inheritance, the
  tree view, comparing two checks, search, settings + database
  maintenance pages.
- [Authentication](auth.md) — optional Entra ID sign-in: app
  registration walkthrough, group-based admins, what a non-admin sees,
  troubleshooting.

## When things go wrong

- [Troubleshooting](troubleshooting.md) — common errors and how to fix
  them (missing `ENCRYPTION_KEY`, scope-casing surprises, REINDEX
  permission errors, etc.).

---

## What is this app for?

Periodic access review on an Azure tenant. RBAC Lens only reads Azure,
through a read-only service principal (`sp-rbaclens-reader`).

Every day it checks the whole tenant (or any management group,
subscription, resource group or single resource you pick). A check walks
the descendants, lists the role assignments and PIM eligibilities at
each, resolves who the identities are through Microsoft Graph, and keeps
the result. Every hour it collects each subscription's Activity Log.
Then the UI lets you:

- See who can do what on each place, in five plain capability
  levels, with grants given here, inherited from above, and given inside.
- Follow the changes: who got or lost access, when, and who did it.
- Run monthly reviews: the person responsible for a place decides
  Keep / Remove / Change / Ask for each grant, starting from a suggested
  decision. Finishing a review turns removals and changes into to-dos
  that the next checks close.
- Read advice on every grant and identity ("What to do"), from fixed
  rules, with optional AI wording.
- Work through what needs a look (findings) across the tenant.
- Look up a person or app and every place they hold a role, directly
  or through a group, and what they actually did.
- Browse the Role guide: every role seen, what it allows, where it is
  used, and why a role is risky.
- Print an assessment report mapped to ISO/IEC 27001:2022.
- Run the app itself: schedules and recent runs (Data & schedules),
  responsible people, notifications, sign-in, credentials (encrypted at
  rest), the audit log and database maintenance (Settings).

## Stack at a glance

| Layer    | Choice                                                 |
|----------|--------------------------------------------------------|
| Backend  | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic          |
| Frontend | React 19 + TypeScript + Vite 7 + Node 24 LTS, Tailwind CSS 4, Untitled UI React, React Aria Components, recharts |
| Database | PostgreSQL 18, JSONB for hierarchy + raw SDK payloads  |
| Auth     | Azure SP (`sp-rbaclens-reader`, read-only) — env vars or encrypted DB row; optional Entra ID sign-in for the UI |
| Crypto   | Fernet (cryptography) for the stored client secret and AI key |
| Email    | Mailpit (catches everything) by default; Microsoft Graph optional |
| Packaging| Docker multi-stage, docker-compose for `test` / `prod` |
