# The Makefile recipe

How this project runs its Docker stacks, and a recipe other projects can
reuse: one `Makefile` at the repo root, `make <command> <test|prod>`, the
same words and the same safety rules everywhere. Copy this file into a new
project as `docs/README-MAKE.md`, start from the skeleton in §4, and the
project behaves the same on a workstation and on a server.

---

## 1. Principles

- **One entry point.** Everything an operator types goes through `make`:
  no wrapper shell script next to it, no "remember the compose flags". The
  Makefile is also the documentation of what can be done (`make help`).
- **The environment is a positional word and it is required**:
  `make up prod`, `make logs test`. A stack command typed without it refuses
  and prints the help — nothing hits production by accident, and nothing
  defaults to production. The legacy `make ENV=prod up` keeps working.
- **`test` and `prod` only.** `test` = the workstation stack (hot reload,
  debug logs, exposed ports, mail catchers); `prod` = the server. No third
  environment unless the project really has one.
- **Every target has a one-line `## description`**; `make help` is generated
  from them and sorted. If a target has no description it does not exist for
  the operator.
- **Safe by default, destructive only on purpose.** Data-destroying targets
  (`purge`, `reset`, `restore`) ask for an explicit confirmation and say in
  their description what they destroy. Volumes are never touched by cleanup
  targets.
- **First run works.** `make up <env>` creates the env file from its example
  and generates the secrets the stack needs (tokens, encryption keys) — the
  operator fills in only what a human must know (credentials, hostnames).
- **No secrets in git**, ever: env files are ignored, `.example` templates are
  committed, generated secrets land in the env file on the host.
- **Thin targets.** A target is one or two commands; anything longer is a
  script under `tools/` or `scripts/` that the target calls (and that a human
  can run alone).

---

## 2. Standard vocabulary

Use these names when the project has the capability — operators move between
projects and should not relearn words.

| Target | Meaning | Env word |
|---|---|---|
| `help` | List targets (default goal) | — |
| `up` | Start the stack (build if needed), detached; then a health check | required |
| `down` | Stop and remove containers, **keep volumes** | required |
| `restart` | `down` + `up` (or restart one service when that is what the project needs) | required |
| `build` | Force-rebuild the images (no cache) | required |
| `pull` | Pull the upstream images (projects on a vendor image) | required |
| `logs` | Tail logs; `SERVICE=<name>` for one service | required |
| `status` / `ps` | Running containers | required |
| `health` | Container health + the URL to open | required |
| `shell` | Shell inside a container (`SERVICE=` to pick) | required |
| `backup` | Consistent backup of the data to `backups/` — safe while running | required |
| `restore` | Restore from `BACKUP=<file>` — destructive, confirms | required |
| `clean` | Remove containers + local images, keep volumes — confirms | required |
| `purge` / `reset` | Remove **everything incl. volumes** — confirms with `yes` | required |
| `env` | Create the env file from its example if missing | required |
| `env-check` | Compare the *running* container's environment with the env file (Docker injects env only at creation) | required |
| `genkey` | Print a random secret to paste into an env file | — |
| `docker-clean` | Reclaim build cache, dangling images, stopped containers — volumes untouched | — |
| `check` | Run the test suites in throwaway containers (**`test` is the env word, so tests are `check`**) | — |
| `check-local` | Same with the local toolchain | — |
| `dev-*` | Start a local, non-Docker server for development (`dev-backend`, `dev-frontend`) | — |
| `release` / `release-dry` | Cut a release (`V=X.Y.Z`, plain integers, the patch a running counter): changelog heading, version bump, `dev` → `main`, tag, push | — |
| `validate` | Validate the project's data files (registries, workflows…) — also the pre-commit hook | — |
| `export` / `import` | Project ↔ tool round trip (e.g. workflows ↔ a workflow engine) | required |
| `test-*` / `diag` | Self-tests and diagnostics against a running stack | required |

Project-specific targets are fine (`smtp-cred`, `email-routing`…) — same
rules: env word, `##` description, thin.

---

## 3. Mechanics worth copying as-is

**Environment from a positional word.** `test` and `prod` are declared as
targets that do nothing except, when typed alone, explain themselves:

```make
ENV_PARAM := $(filter test prod,$(MAKECMDGOALS))
ENV       ?= $(lastword $(ENV_PARAM))
.PHONY: test prod
test prod:
	@if [ "$(words $(MAKECMDGOALS))" = "1" ]; then \
	  echo "'$@' is an environment, not a command. Usage: make <command> $@"; $(MAKE) --no-print-directory help; fi
```

**The guard** every stack target depends on:

```make
_env-guard:
	@if [ -z "$(ENV)" ]; then echo "!! No environment specified: make <command> test|prod"; $(MAKE) --no-print-directory help; exit 1; fi
	@if [ "$(ENV)" != "test" ] && [ "$(ENV)" != "prod" ]; then echo "!! ENV must be test or prod, got '$(ENV)'"; exit 1; fi
```

**Deferred variables** (`=`, not `:=`) for anything that depends on `ENV`, so
they resolve after the word is known: the compose command line, the env file,
ports read from it.

```make
COMPOSE  = docker compose -p $(PROJECT)-$(ENV) -f docker-compose.yml -f docker-compose.$(ENV).yml
PORT     = $(or $(shell grep -E '^FRONTEND_PORT=' .env 2>/dev/null | cut -d= -f2-),3000)
```

Two patterns for the env file — pick the one the project uses:
- **one `.env` + overlays**: `docker-compose.yml` + `docker-compose.<env>.yml`, a single `.env`;
- **one env file per environment** (this repo): `.env.test` / `.env.prod` passed with `--env-file`, `.example` templates for both.

**Generated help** from the `##` comments:

```make
help: ## Show this help (usage: make <command> <test|prod>)
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort \
	  | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'
```

**Secrets on first run** — a `define` called once per secret, generating only
when the value is missing, empty or still the example placeholder:

```make
define ensure-secret
	cur=$$(grep -E '^$(1)=' .env 2>/dev/null | cut -d= -f2-); \
	if [ -z "$$cur" ] || [ "$$cur" = "$(2)" ]; then \
	  val=$$(openssl rand -hex 32); \
	  if grep -qE '^$(1)=' .env; then sed -i.bak "s/^$(1)=.*/$(1)=$$val/" .env && rm -f .env.bak; \
	  else printf '\n$(1)=%s\n' "$$val" >> .env; fi; echo "Generated $(1) ($(3))"; fi
endef
```

**Backups that are actually consistent.** Never `docker cp` a live database
file: SQLite in WAL mode keeps the newest writes in the `-wal` sidecar until a
checkpoint, and a plain copy drops them. Run the engine's own backup inside the
container (SQLite online backup API via a small script piped to
`docker exec -i … python3 -`; `pg_dump` for Postgres; the tool's export for
n8n) and copy the result out.

**Confirmations** for destructive targets, in the recipe itself:

```make
purge: _env-guard ## DANGER: remove EVERYTHING incl. the database volume — asks for 'yes'
	@read -rp "Type 'yes' to confirm: " c; [ "$$c" = "yes" ] && $(COMPOSE) down -v --rmi local || echo "Aborted."
```

**Default goal is `help`**, `SHELL := /bin/bash` when recipes use `read -rp`
or `[[ ]]`, `.PHONY` on everything (no target is a file).

---

## 4. Skeleton to start from

```make
# <product> — <one line>.  Usage: make <command> <test|prod>   (`make` alone = help)
ENV_PARAM := $(filter test prod,$(MAKECMDGOALS))
ENV       ?= $(lastword $(ENV_PARAM))
PROJECT   := <product>
COMPOSE    = docker compose -p $(PROJECT)-$(ENV) -f docker-compose.yml -f docker-compose.$(ENV).yml
SERVICE   ?= <main-service>
SHELL     := /bin/bash
.DEFAULT_GOAL := help

.PHONY: test prod _env-guard _env-file help up down restart build logs status ps health shell backup clean purge check genkey docker-clean
test prod:
	@if [ "$(words $(MAKECMDGOALS))" = "1" ]; then echo "'$@' is an environment, not a command. Usage: make <command> $@"; $(MAKE) --no-print-directory help; fi
_env-guard:
	@if [ -z "$(ENV)" ]; then echo "!! No environment specified: make <command> test|prod"; $(MAKE) --no-print-directory help; exit 1; fi
	@if [ "$(ENV)" != "test" ] && [ "$(ENV)" != "prod" ]; then echo "!! ENV must be test or prod"; exit 1; fi
_env-file:
	@test -f .env || { cp .env.example .env && echo "Created .env — fill in the credentials"; }

help: ## Show this help (usage: make <command> <test|prod>)
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'
up: _env-guard _env-file ## Start the stack (build if needed), then health check
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory health ENV=$(ENV)
down: _env-guard ## Stop the stack (volumes kept)
	$(COMPOSE) down
restart: _env-guard ## Down + up
	$(COMPOSE) down && $(MAKE) --no-print-directory up ENV=$(ENV)
build: _env-guard _env-file ## Force-rebuild images (no cache)
	$(COMPOSE) build --no-cache
logs: _env-guard ## Tail logs (SERVICE=<name> for one)
	$(COMPOSE) logs -f $(if $(filter command line,$(origin SERVICE)),$(SERVICE),)
status ps: _env-guard ## Running containers
	$(COMPOSE) ps
health: _env-guard ## Container health + URL
	@$(COMPOSE) ps --format '  {{.Service}}: {{.Status}}'
shell: _env-guard ## Shell in a container (SERVICE=<name>)
	$(COMPOSE) exec $(SERVICE) sh
backup: _env-guard ## Consistent backup to backups/ (engine's own backup, not a file copy)
	@echo "TODO: run the engine backup inside the container, docker cp the result to backups/"
clean: _env-guard ## Remove containers + local images (volumes kept) — asks first
	@read -rp "Remove the $(ENV) containers + images? [y/N] " c; [[ "$$c" =~ ^[Yy]$$ ]] && $(COMPOSE) down --rmi local || echo "Aborted."
purge: _env-guard ## DANGER: remove EVERYTHING incl. volumes — asks for 'yes'
	@read -rp "Type 'yes' to confirm: " c; [ "$$c" = "yes" ] && $(COMPOSE) down -v --rmi local || echo "Aborted."
check: ## Run the test suites in throwaway containers
	@echo "TODO"
genkey: ## Print a random 32-byte hex secret
	@openssl rand -hex 32
docker-clean: ## Reclaim build cache / dangling images / stopped containers (volumes untouched)
	docker container prune -f && docker image prune -f && docker builder prune -f
```

---

## 5. Conventions around the Makefile

- **Container names** `<product>-<service>-<env>` — pick one scheme and keep it; runbooks quote them.
- **Compose project name** `-p <product>-<env>` so `test` and `prod` never share volumes or networks on the same host.
- **On the server**: repo in one place (e.g. `/opt/<product>`), run `make … prod` from there; a cron job calls `make <target> prod` with an absolute path to the repo.
- **Docs quote `make` commands verbatim** (README, quick start, recovery runbook); when a target is renamed, grep the docs.
- **CLAUDE.md** lists the targets under *Build & Run Commands* with the env-word rule stated once.
- **Changelog entry** when the operator-facing commands change (that is a behaviour change for whoever runs the stack).

---

## 6. Checklist for a new project

```
[ ] Makefile at repo root, help as default goal        [ ] env word required (_env-guard), test|prod only
[ ] every target has a ## description                   [ ] up creates env file + secrets on first run
[ ] destructive targets confirm, volumes never cleaned  [ ] backup = engine backup, not a file copy
[ ] check / check-local for the test suites             [ ] docs + CLAUDE.md quote make commands verbatim
[ ] no wrapper script next to the Makefile              [ ] .env* ignored, .example committed
```

---

## 7. In this repo (RBAC Lens)

The [Makefile](../Makefile) follows the recipe with one env file per
environment (`.env.test` / `.env.prod`, passed with `--env-file`) and the
compose files under `docker/`. `make help` lists everything; beyond the
standard words it has:

| Target | What it does | Env word |
|---|---|---|
| `migrate` | `alembic upgrade head` in the backend (`up` also does it on start) | required |
| `psql` | psql on the database; `ARGS="-c '…'"` for one query | required |
| `auth-off` | Turn sign-in off from the host after a wrong setting locked everyone out; type the env name to confirm | required |
| `scan` | Trivy over the stack's images; fails on a fixable CRITICAL, exits 2 when it could not scan | required |
| `whoami` | Which Azure identity the env file gives the stack (first characters of the ids) | required |
| `lock` | Regenerate `backend/requirements*.lock` and `frontend/package-lock.json` | — |
| `lint` | Ruff on the backend, ESLint on the frontend (part of `check`) | — |

`check` runs lint, pytest on SQLite, then pytest on a throwaway Postgres
(`docker/docker-compose.check.yml`, every migration from empty), then
`tsc` and Vitest. There is no `check-local`, `dev-*`, `pull`, `validate`
or `export` / `import`.

Arguments: `SERVICE=` (logs, shell), `BACKUP=` (restore), `ARGS=` (check,
psql), `V=X.Y.Z` (release). The Azure credentials are `ARM_*` in the
env file, like every other setting. `release` calls `scripts/release.sh`, which also refreshes
`frontend/public/CHANGELOG.md` and pushes only when an `origin` remote
exists.
