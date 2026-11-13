# RBAC Lens — Azure RBAC access review.   Usage: make <command> <test|prod>   (`make` alone = help)
#
# One entry point, on the Makefile recipe (docs/README-MAKE.md): the environment
# word is required for every stack command — nothing defaults to prod.
#   test  workstation stack: hot reload, debug logs, ports 5173 / 8000 / 5432 on 127.0.0.1
#   prod  built images behind nginx on port 80
#
# Env file per environment: .env.test / .env.prod, created from the matching
# .example on first `make up` with a generated ENCRYPTION_KEY. The Azure
# credentials (ARM_*) live in the env file too, or on the Settings page;
# `make restart <env>` applies a change.
# Named arguments (NAME=value) are listed by `make help`.

ENV_PARAM := $(filter test prod,$(MAKECMDGOALS))
ENV       ?= $(lastword $(ENV_PARAM))
PROJECT   := rbaclens
ENV_FILE   = .env.$(ENV)
# The env file is tested by the shell when the command runs, not by $(wildcard): make caches
# directory listings, so a file created earlier in the same run (first `make up`) looks absent.
COMPOSE    = docker compose -p $(PROJECT)-$(ENV) --project-directory . \
             $$([ -f $(ENV_FILE) ] && echo --env-file $(ENV_FILE)) \
             -f docker/docker-compose.yml -f docker/docker-compose.$(ENV).yml
SERVICE   ?= backend
# Only honoured when typed on the command line, never from a stray shell variable.
SVC        = $(if $(filter command line,$(origin SERVICE)),$(SERVICE))
BACKUP_DIR := backups
TRIVY_VERSION := 0.76.2
# Pinned by digest too: a re-pushed tag can't swap the scanner. To update, pull the
# new tag and take `docker image inspect --format '{{index .RepoDigests 0}}'`.
TRIVY_IMAGE   := aquasec/trivy:$(TRIVY_VERSION)@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb
UV_VERSION    := 0.13.7
UI_URL     = $(if $(filter prod,$(ENV)),http://localhost,http://localhost:5173)
API_BASE   = $(if $(filter prod,$(ENV)),http://localhost/api,http://localhost:8000/api)
API_HEALTH = $(API_BASE)/health
# The address prod's port 80 is published on (BIND_ADDRESS in .env.prod; default: this
# machine only). The test stack is always on 127.0.0.1.
BIND       = $$([ "$(ENV)" = prod ] && grep -E '^BIND_ADDRESS=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-)
# Says so when the env file has no Azure credentials (the Settings page may still have some).
CREDS      = grep -qE '^ARM_CLIENT_ID=.+' $(ENV_FILE) || \
               echo "==> No ARM_* in $(ENV_FILE): checks use the credentials saved on the Settings page, if any" >&2
SHELL     := /bin/bash
.DEFAULT_GOAL := help

.PHONY: test prod _env-guard _env-file help up down restart build logs status ps health shell psql \
        migrate backup auth-off scan restore env env-check clean purge check lint whoami genkey docker-clean \
        lock release release-dry public-check hooks denylist _trivy-image

# Environment words are arguments, not commands; typed alone they explain themselves.
test prod:
	@if [ "$(words $(MAKECMDGOALS))" = "1" ]; then \
	  echo "'$@' is an environment, not a command. Usage: make <command> $@   e.g. make up $@"; \
	  $(MAKE) --no-print-directory help; fi

_env-guard:
	@if [ -z "$(ENV)" ]; then echo "!! No environment specified: make <command> test|prod"; $(MAKE) --no-print-directory help; exit 1; fi
	@if [ "$(ENV)" != "test" ] && [ "$(ENV)" != "prod" ]; then echo "!! ENV must be test or prod, got '$(ENV)'"; exit 1; fi

# A Fernet key: 32 random bytes, URL-safe base64 (44 chars). Not hex — the app rejects that.
FERNET     = openssl rand -base64 32 | tr '+/' '-_'

_env-file: _env-guard
	@test -f $(ENV_FILE) || { cp $(ENV_FILE).example $(ENV_FILE) && echo "Created $(ENV_FILE) from $(ENV_FILE).example"; }
	@chmod 600 $(ENV_FILE)
	@b=$(BIND); if [ -n "$$b" ] && ! echo "$$b" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$$'; then \
	  echo "!! BIND_ADDRESS in $(ENV_FILE) must be an IPv4 address (127.0.0.1 = this machine only, 0.0.0.0 = the network), got '$$b'"; exit 1; fi
	@cur=$$(grep -E '^ENCRYPTION_KEY=' $(ENV_FILE) | cut -d= -f2-); \
	if [ -z "$$cur" ]; then val=$$($(FERNET)); \
	  if grep -qE '^ENCRYPTION_KEY=' $(ENV_FILE); then sed -i.bak "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$$val|" $(ENV_FILE) && rm -f $(ENV_FILE).bak; \
	  else printf '\nENCRYPTION_KEY=%s\n' "$$val" >> $(ENV_FILE); fi; \
	  echo "Generated ENCRYPTION_KEY in $(ENV_FILE) (encrypts credentials saved on the Settings page)"; fi
	@cur=$$(grep -E '^POSTGRES_PASSWORD=' $(ENV_FILE) | cut -d= -f2-); \
	if [ -z "$$cur" ] || [ "$$cur" = "change-me" ]; then val=$$(openssl rand -hex 24); \
	  rest=$$(grep -vE '^POSTGRES_PASSWORD=' $(ENV_FILE)); \
	  printf '%s\nPOSTGRES_PASSWORD=%s\n' "$$rest" "$$val" > $(ENV_FILE); \
	  echo "Generated POSTGRES_PASSWORD in $(ENV_FILE) (make up sets it on the database)"; fi

# Sets the database role's password to POSTGRES_PASSWORD from the env file, so a generated or
# changed value also works on an existing volume. Goes through the container's local socket, which
# needs no password; the value travels as a psql variable, never on a command line.
DB_PASSWORD = printf '%s\n' "ALTER ROLE CURRENT_USER PASSWORD :'pw';" | $(COMPOSE) exec -T db sh -c \
              'psql -q -v ON_ERROR_STOP=1 -v pw="$$POSTGRES_PASSWORD" -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

help: ## Show this help (usage: make <command> <test|prod>)
	@echo "Usage: make <command> <test|prod>          (\`make\` alone = help)"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'
	@echo "Arguments:"
	@echo "  SERVICE=backend|frontend|db   logs / shell (shell defaults to backend)"
	@echo "  BACKUP=<file>                 restore: the dump to restore (from backups/)"
	@echo "  ARGS=\"...\"                    check: extra pytest args   psql: extra psql args"
	@echo "  V=X.Y.Z                       release / release-dry: the version to cut"

# --- stack commands (need the env word) ---------------------------------------------

up: _env-file ## Start the stack (creates the env file + key on first run), then health
	@$(COMPOSE) up -d --wait db
	@$(DB_PASSWORD)
	@$(CREDS); $(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory health ENV=$(ENV)

down: _env-guard ## Stop and remove the containers (volumes kept)
	@$(COMPOSE) down

restart: _env-file ## Down + up — recreates containers, so env file / credential changes apply
	@$(COMPOSE) down
	@$(MAKE) --no-print-directory up ENV=$(ENV)

build: _env-file ## Rebuild the images from fresh base images (pull, no cache) — then make up <env>
	@$(COMPOSE) pull --ignore-buildable
	@$(COMPOSE) build --pull --no-cache

# The pinned Trivy image, for the release workflow (.github/workflows/release.yml).
_trivy-image:
	@echo $(TRIVY_IMAGE)

scan: _env-file ## Scan the stack's images for known vulnerabilities (Trivy); fails on fixable CRITICAL ones
	@trivy() { docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v rbaclens-trivy-cache:/root/.cache \
	    $(TRIVY_IMAGE) image --quiet --scanners vuln --ignore-unfixed "$$@"; }; \
	crit=0; broken=0; \
	for img in $$($(COMPOSE) config --images); do \
	  echo "==> $$img"; \
	  if ! out=$$(trivy --severity HIGH,CRITICAL "$$img" 2>&1); then \
	    echo "!! Trivy could not scan it:"; printf '%s\n' "$$out" | tail -5 | sed 's/^/   /'; broken=1; continue; fi; \
	  printf '%s\n' "$$out" | grep -E '│ +[1-9][0-9]* +│$$' || echo "   no fixable HIGH or CRITICAL"; \
	  trivy --severity CRITICAL --exit-code 3 "$$img" >/dev/null 2>&1; rc=$$?; \
	  case $$rc in 0) ;; 3) crit=1;; *) echo "!! Trivy failed on the CRITICAL check (exit $$rc)"; broken=1;; esac; \
	done; \
	[ $$broken = 0 ] || { echo "!! Some images were not scanned: the result is incomplete."; exit 2; }; \
	[ $$crit = 0 ] && echo "No fixable CRITICAL vulnerability." || { echo "!! fixable CRITICAL vulnerabilities: make build $(ENV), then make up $(ENV)"; exit 1; }

logs: _env-guard ## Tail logs (SERVICE=backend|frontend|db for one)
	@$(COMPOSE) logs -f --tail 200 $(SVC)

status: _env-guard ## Running containers
	@$(COMPOSE) ps

ps: status

health: _env-guard ## Container health, the API health check and the URL to open
	@$(COMPOSE) ps --format '  {{.Name}}: {{.Status}}'
	@for i in $$(seq 1 20); do out=$$(curl -fsS $(API_HEALTH) 2>/dev/null) && break; sleep 2; done; \
	if [ -n "$$out" ]; then echo "  api: $$out"; else echo "  api: not answering at $(API_HEALTH) (make logs $(ENV) SERVICE=backend)"; fi
	@bind=$(BIND); bind=$${bind:-127.0.0.1}; \
	case "$$bind" in 127.*) where="this machine only";; *) where="the network ($$bind)";; esac; \
	case "$$(curl -fsS $(API_BASE)/auth/config 2>/dev/null)" in \
	  *'"enabled":true'*)  echo "  sign-in: on, the app answers on $$where";; \
	  *'"enabled":false'*) case "$$bind" in \
	      127.*) echo "  sign-in: off, the app answers on $$where";; \
	      *) echo "  !! sign-in is off and the app answers on $$where: anyone who reaches it can read and change everything"; \
	         echo "     turn sign-in on (Settings > Authentication), or remove BIND_ADDRESS from $(ENV_FILE)";; \
	    esac;; \
	  *) echo "  sign-in: unknown (the API did not answer)";; \
	esac
	@echo "  open: $(UI_URL)"

shell: _env-guard ## Shell in a container (SERVICE=, default backend)
	@$(COMPOSE) exec $(SERVICE) sh -c 'command -v bash >/dev/null 2>&1 && exec bash || exec sh'

psql: _env-guard ## psql on the database (ARGS="-c '...'" for a one-shot query)
	@$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" "$$@"' -- $(ARGS)

migrate: _env-guard ## Apply database migrations (alembic upgrade head; up also does it on start)
	@$(COMPOSE) exec backend alembic upgrade head

backup: _env-guard ## Consistent database backup (pg_dump -Fc) to backups/<env>-<timestamp>.dump, readable by you only — safe while running
	@mkdir -p $(BACKUP_DIR)
	@umask 077; f=$(BACKUP_DIR)/$(ENV)-$$(date +%Y%m%d-%H%M%S).dump; \
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > $$f && \
	echo "Backup written: $$f ($$(du -h $$f | cut -f1))" || { rm -f $$f; echo "!! backup failed"; exit 1; }

auth-off: _env-guard ## Turn sign-in off from the host (after a wrong sign-in setting locked everyone out) — type the env name to confirm
	@read -rp "Turn sign-in OFF for $(ENV)? Everyone who can reach the app becomes admin. Type '$(ENV)' to confirm: " c; \
	[ "$$c" = "$(ENV)" ] && $(COMPOSE) exec -T backend python -m scripts.auth_off || echo "Aborted."

restore: _env-guard ## DANGER: replace the database with BACKUP=<file> — type the env name to confirm
	@[ -n "$(BACKUP)" ] && [ -f "$(BACKUP)" ] || { echo "!! usage: make restore $(ENV) BACKUP=backups/<file>.dump"; ls -1t $(BACKUP_DIR)/$(ENV)-*.dump 2>/dev/null | head -5; exit 1; }
	@read -rp "This REPLACES the $(ENV) database with $(BACKUP). Type '$(ENV)' to confirm: " c; [ "$$c" = "$(ENV)" ] || { echo "Aborted."; exit 1; }
	@$(COMPOSE) stop backend frontend
	@# One transaction: empty the schema (an older dump must not leave newer tables
	@# behind for the next migration to trip on), then load the dump. COMMIT is
	@# sent only when pg_restore read the whole file; otherwise a deliberate error
	@# stops psql, the transaction rolls back and the database is as it was.
	@$(COMPOSE) exec -T db sh -c '{ echo "BEGIN; SET client_min_messages = warning; DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner; GRANT USAGE ON SCHEMA public TO PUBLIC;"; \
	  pg_restore --no-owner -f - && echo "COMMIT;" || { echo; echo "\\."; echo "SELECT 1/0;"; }; } | psql -q -X -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" >/dev/null' < $(BACKUP) \
	  || { $(COMPOSE) start backend frontend; echo "!! restore failed — nothing was changed"; exit 1; }
	@$(COMPOSE) start backend frontend
	@echo "Restored $(BACKUP) into $(ENV). Migrations run on backend start."

env: _env-file ## Create the env file from its example if missing (and generate ENCRYPTION_KEY)

env-check: _env-guard ## Compare the running containers' environment with the env file (values never printed)
	@test -f $(ENV_FILE) || { echo "!! $(ENV_FILE) missing (make env $(ENV))"; exit 1; }
	@cid=$$($(COMPOSE) ps -q backend); [ -n "$$cid" ] || { echo "!! backend not running (make up $(ENV))"; exit 1; }; \
	dbid=$$($(COMPOSE) ps -q db); \
	grep -E '^[A-Z_]+=' $(ENV_FILE) | while IFS= read -r line; do \
	  k=$${line%%=*}; v=$${line#*=}; \
	  case "$$k" in POSTGRES_*) c=$$dbid ;; *) c=$$cid ;; esac; \
	  r=$$(docker exec $$c printenv "$$k" 2>/dev/null </dev/null); \
	  if [ -z "$$v" ]; then st="unset in file$$( [ -n "$$r" ] && echo ' (container has a value — from compose)')"; \
	  elif [ "$$r" = "$$v" ]; then st="same"; else st="DIFFERS — make restart $(ENV) to apply"; fi; \
	  printf "  %-22s %s\n" "$$k" "$$st"; done

clean: _env-guard ## Remove the containers + local images (volumes kept) — asks first
	@read -rp "Remove the $(ENV) containers and images (database kept)? [y/N] " c; [[ "$$c" =~ ^[Yy]$$ ]] && $(COMPOSE) down --rmi local || echo "Aborted."

purge: _env-guard ## DANGER: remove EVERYTHING incl. the database volume — asks for 'yes'
	@read -rp "This DELETES the $(ENV) database volume. make backup $(ENV) first. Type 'yes' to confirm: " c; \
	[ "$$c" = "yes" ] && $(COMPOSE) down -v --rmi local || echo "Aborted."

# --- repo-wide commands (no env word) -----------------------------------------------

# Throwaway containers from the test images. The tests use in-memory SQLite — and the backend
# entrypoint (which waits for the database to migrate) is bypassed. Then tests/pg runs on a
# throwaway Postgres (docker-compose.check.yml): every migration from empty, the schema
# compared with the models, and the app's tests again.
CHECK      = docker compose -p $(PROJECT)-check --project-directory . -f docker/docker-compose.yml -f docker/docker-compose.test.yml
CHECK_PG   = $(CHECK) -f docker/docker-compose.check.yml
PG_CHECK_URL = postgresql+psycopg://rbaclens:check-only@pgcheck:5432/rbaclens_check

check: ## Lint, then the test suites in throwaway containers: pytest (SQLite, then Postgres with every migration), tsc, vitest (ARGS= extra pytest args)
	@$(CHECK) build -q backend frontend
	@$(MAKE) --no-print-directory lint
	@$(CHECK) run --rm --no-deps -T --entrypoint '' backend pytest -q $(ARGS)
	@$(CHECK_PG) up -d --wait pgcheck >/dev/null 2>&1 || { echo "!! the throwaway Postgres did not start"; exit 1; }
	@$(CHECK_PG) run --rm --no-deps -T -e PG_CHECK_URL=$(PG_CHECK_URL) --entrypoint '' backend pytest -q tests/pg; \
	  rc=$$?; $(CHECK_PG) rm -sfv pgcheck >/dev/null 2>&1; exit $$rc
	@$(CHECK) run --rm --no-deps -T frontend sh -c 'npx tsc -b --noEmit && npx vitest run'

lint: ## Ruff (rules and format) on the backend, ESLint (TypeScript, React hooks, accessibility) on the frontend; part of check
	@$(CHECK) run --rm --no-deps -T --entrypoint '' backend sh -c 'ruff check --output-format concise app tests scripts alembic && ruff format --check app tests scripts alembic'
	@$(CHECK) run --rm --no-deps -T frontend npx eslint .

whoami: _env-guard ## Which Azure identity the env file gives the stack (first characters of the ids only)
	@id=$$(grep -E '^ARM_CLIENT_ID=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	t=$$(grep -E '^ARM_TENANT_ID=' $(ENV_FILE) 2>/dev/null | tail -1 | cut -d= -f2-); \
	if [ -n "$$id" ]; then echo "$(ENV_FILE): client $${id:0:8}... (tenant $${t:0:8}...)"; \
	else echo "None in $(ENV_FILE): set ARM_* there, or save them on the Settings page"; fi

genkey: ## Print a new ENCRYPTION_KEY (Fernet)
	@$(FERNET)

docker-clean: ## Reclaim build cache, dangling images, stopped containers (volumes untouched)
	@docker container prune -f && docker image prune -f && docker builder prune -f

lock: ## Regenerate the lockfiles (backend/requirements*.lock, frontend/package-lock.json) after a dependency change
	@docker run --rm -v "$$PWD/backend:/src" -w /src python:3.12-slim sh -c '\
	  pip install -q --root-user-action=ignore uv==$(UV_VERSION) && \
	  for x in "" dev; do o=requirements$${x:+-$$x}.lock; \
	    uv pip compile pyproject.toml $${x:+--extra $$x} --universal --python-version 3.12 --generate-hashes \
	      --custom-compile-command "make lock" -q -o $$o && echo "backend/$$o"; done'
	@docker run --rm -v "$$PWD/frontend:/src" -w /src node:24-alpine \
	  npm install --package-lock-only --no-audit --no-fund --ignore-scripts --loglevel=error && echo "frontend/package-lock.json"
	@echo "Then: make check, and make build <env> to install them."

release: ## Release V=X.Y.Z: changelog heading, version bump, dev → main (fast-forward), tag, push — asks first
	@[ -n "$(V)" ] || { echo "!! usage: make release V=1.0.0"; exit 1; }
	@./scripts/release.sh $(V)

release-dry: ## Preview a release (V=X.Y.Z): shows the planned changes, touches nothing
	@[ -n "$(V)" ] || { echo "!! usage: make release-dry V=1.0.0"; exit 1; }
	@./scripts/release.sh $(V) --dry-run

# --- publishing (private names stay in .git/info/, see scripts/public_check.py) -------

denylist: _env-guard ## Refresh the private names (people, groups, places, ids) from the env's database into .git/info/denylist-db
	@$(COMPOSE) exec -T db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -At' < scripts/denylist.sql | python3 scripts/public_check.py denylist

public-check: ## Look for private names (.git/info/denylist*) in the tracked files
	@python3 scripts/public_check.py tree

hooks: ## Install the pre-push check (refuses private names, old history, non-noreply emails)
	@git config core.hooksPath scripts/git-hooks && echo "==> pre-push check installed (scripts/git-hooks/pre-push)"
