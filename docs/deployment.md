# Deployment

## Stages

A base compose file and one overlay per use (services: db, backend,
frontend, mailpit, and in prod a backup job):

| File                                 | Role  | Notes                                          |
|--------------------------------------|--------|------------------------------------------------|
| [`docker/docker-compose.yml`](../docker/docker-compose.yml) | base | Shared service shapes (db / backend / frontend) and common env vars. Always merged with one of the overlays below. |
| [`docker/docker-compose.test.yml`](../docker/docker-compose.test.yml) | `test` overlay | Hot-reload, source-mounted volumes, ports 5432 / 8000 / 5173 (and Mailpit 8026) on 127.0.0.1. |
| [`docker/docker-compose.prod.yml`](../docker/docker-compose.prod.yml) | `prod` overlay | Built artifact targets, nginx fronting the SPA on `:80`, no source mounts, named volume for Postgres, `restart: unless-stopped` on every service, the `backup` service. |
| [`docker/docker-compose.check.yml`](../docker/docker-compose.check.yml) | `make check` only | A throwaway Postgres in memory for the migration and Postgres tests. |

The `Makefile` always passes both files, the project name
`rbaclens-<env>` and the env file `.env.<env>`; use it rather than raw
`docker compose`:

```bash
make up prod          # build, start, health check
make status prod
make logs prod SERVICE=backend
```

On a Linux server, keep the repo in one place (for example
`/opt/rbaclens`) and run every command from there as
`make <command> prod`; cron jobs call `make <target> prod` with the
absolute path. Containers are named `rbaclens-<service>-prod`.

## Required environment

Each stack reads its own env file at the project root: `.env.prod` or
`.env.test`, created by `make up <env>` (or `make env <env>`) from the
matching `.example`, with `ENCRYPTION_KEY` and `POSTGRES_PASSWORD` generated
when empty.

The Makefile passes the env file to `docker compose --env-file`, which only
fills `${VAR}` in the compose files. **A variable reaches the backend only
when the compose files list it under `backend.environment`.** These do:

| Variable             | Purpose |
|----------------------|---------|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database owner, password and name (default `rbaclens`, generated, `rbaclens_prod`). `make up` sets the password on the database each start, so changing it in the file is enough. The backend's `DATABASE_URL` is built from them. |
| `ARM_CLIENT_ID` / `ARM_CLIENT_SECRET` / `ARM_TENANT_ID` | The Azure identity (see [Azure identity](#azure-identity-least-privilege)). Empty: the credentials saved on the Settings page are used. \* |
| `ENCRYPTION_KEY`     | Fernet key encrypting the secrets saved in the app (Azure client secret, Anthropic API key). \*\* |
| `CORS_ORIGINS`       | Prod: JSON array of allowed origins, default `["http://localhost"]`. The test stack fixes `["http://localhost:5173"]`. |
| `REQUESTS_CA_BUNDLE` | In-container path to a corporate CA bundle. See [Corporate TLS / proxy CA](#corporate-tls--proxy-ca). |
| `EXTRA_SERVER_NAMES` | Host names the site answers to besides `localhost`, `*.local` and IP addresses (space-separated); read by the backend and the prod nginx. |
| `TZ`                 | Time zone of the backend (and the backup job), e.g. `Europe/Zurich`. The daily check of the whole tenant runs at 06:00 in it. Unset = UTC. |
| `NOTIFICATION_CHANNEL` | `smtp` by default in both stacks (to Mailpit); `email` sends through Microsoft Graph; empty = messages wait in the outbox. See [Email notifications](#email-notifications). |
| `SMTP_HOST` / `SMTP_PORT` | Default `mailpit` / `1025`. Plain SMTP, no authentication. |
| `NOTIFICATION_INBOX_URL` | Where to read captured messages; default Mailpit's inbox (`http://localhost:8025` prod, `:8026` test). |
| `NOTIFICATION_SENDER` | For `email`: the mailbox messages are sent from. |
| `NOTIFICATION_ADMIN_EMAIL` | Where messages for the admins' review go; without it they are not sent. Test default `admins@rbaclens.test`. |
| `PUBLIC_URL`         | The app's address, for links in messages. Default `http://localhost` (prod), `http://localhost:5173` (test). |
| `AI_DAILY_LIMIT`     | New AI texts per day at most (default 200; 0 = no limit). |

Read by compose itself, not the backend: `BIND_ADDRESS` (prod port 80, see
[Who can reach the app](#who-can-reach-the-app)), `BACKUP_HOUR` /
`BACKUP_KEEP` (see [Automatic backups](#automatic-backups)). The overlays
fix `STAGE` (`test` / `prod`), `LOG_LEVEL` (`DEBUG` / `INFO`) and, in prod,
`FORWARDED_ALLOW_IPS`.

\* Env vars take precedence over credentials saved on the Settings page.
Leave them empty to use the saved ones.

\*\* Needed only to save secrets in the app. Without it the Settings page
can't store a secret and says so.

### Backend settings

Everything else in `backend/app/core/config.py` keeps its default unless
you add it under `backend: environment:` in the stage overlay
(`docker/docker-compose.<env>.yml`) and run `make restart <env>`. Setting
it in `.env.prod` alone does nothing.

| Setting | Default | Purpose |
|---|---|---|
| `DEFAULT_SCHEDULES` / `DEFAULT_CHECK_HOUR` | `true` / `6` | After the first check, create a daily check of the tenant root at that hour and an hourly Activity Log pull per subscription. Created once; deleted ones stay deleted. |
| `SCHEDULER_TICK_SECONDS` | `60` | How often due schedules are checked; `0` disables the scheduler. |
| `SCHEDULER_MAX_CONCURRENT_JOBS` | `2` | Threads shared by scheduled checks and pulls. |
| `RECOVER_JOBS_AT_START` | `true` | Close jobs a previous process left running and run them again. `false` only when several backends share one database. |
| `SECOND_PASS_HOURS` / `SECOND_PASS_HOUR` | `48` / `3` | Once a night, re-read each subscription's last hours of Activity Log for late events; `0` hours turns it off. |
| `STUCK_SCAN_TIMEOUT_SECONDS` | `3600` | A job with no sign of life (`heartbeat_at`) for this long is marked failed. |
| `STUCK_SCAN_SWEEP_SECONDS` | `60` | How often the sweeper runs; `0` disables it. |
| `RETENTION_INTERVAL_HOURS` / `RETENTION_FIRST_RUN_MINUTES` | `24` / `5` | The retention run ([data-model.md](data-model.md#retention)); `0` hours disables the loop. |
| `EVENT_RETENTION_DAYS`, `SNAPSHOT_KEEP_PER_SCOPE`, `FAILED_SNAPSHOT_RETENTION_DAYS`, `AUDIT_RETENTION_DAYS`, `CHANGE_RETENTION_DAYS`, `PULL_RECORD_RETENTION_DAYS` | `120`, `6`, `7`, `365`, `365`, `30` | What retention deletes; `0` keeps everything. |
| `NOTIFICATION_MAX_AGE_DAYS` | `7` | Waiting messages older than this are closed, not sent late. |
| `ANTHROPIC_API_KEY` | empty | AI wording key used when none is saved in Settings. |

### Generating `ENCRYPTION_KEY`

`make up` and `make env` generate it when the file has none. `make genkey`
prints a new one (a Fernet key: 44 characters of URL-safe base64). Treat it
as a secret: without it, the secrets saved in the app can't be read.

### Rotating `ENCRYPTION_KEY`

`backend/scripts/rotate_encryption_key.py` decrypts every stored secret
(the Azure client secret and the Anthropic API key) with the old key and
re-encrypts it with the new one in one transaction.

```bash
OLD_KEY=$(grep '^ENCRYPTION_KEY=' .env.prod | cut -d= -f2-)
NEW_KEY=$(make -s genkey)

# dry run first: no writes, reports the rows and any decryption error
docker compose -p rbaclens-prod exec -e OLD_KEY="$OLD_KEY" -e NEW_KEY="$NEW_KEY" \
    backend python -m scripts.rotate_encryption_key --dry-run
# then for real
docker compose -p rbaclens-prod exec -e OLD_KEY="$OLD_KEY" -e NEW_KEY="$NEW_KEY" \
    backend python -m scripts.rotate_encryption_key

# put NEW_KEY in .env.prod as ENCRYPTION_KEY, then recreate the containers
make restart prod
```

The script stops before writing anything if any row fails to decrypt with
the old key, so a half-rotated state isn't possible.

## Email notifications

### Mailpit: every message caught, none delivered

Both stacks run [Mailpit](https://mailpit.axllent.org/)
(`axllent/mailpit:v1.33.1`), and the backend sends to it by default
(`NOTIFICATION_CHANNEL=smtp`, `SMTP_HOST=mailpit`). Every message RBAC
Lens would send lands in Mailpit's web inbox and reaches nobody.

- **Prod:** http://localhost:8025, bound to this machine only. Messages
  are kept in the volume `rbaclens-prod_mailpit_prod_data` (newest 500).
- **Test:** http://localhost:8026, in memory.

Settings › Notifications says "Captured in Mailpit" and links to the
inbox. Set `NOTIFICATION_ADMIN_EMAIL` in `.env.prod` to see the admins'
messages too (the test stack uses `admins@rbaclens.test`).

People's addresses come from Entra ID, so without Azure credentials
(no `ARM_*` and none saved on the Settings page) messages to people wait and are
retried. The test message and admin messages still arrive.

### Real email through Microsoft Graph

RBAC Lens emails:
- the person asked about their access (Ask);
- reviewers, when their monthly review opens and three days before it is due;
- reviewers, as a reminder for questions still open after 7 days, and
  about to-dos still open 14 days after their review;
- the admins, when the data stops being kept up to date.

Messages wait in the outbox (Settings › Notifications) until email is
connected.

1. **Permission:** in Entra ID, give the app registration RBAC Lens uses
   the Microsoft Graph *application* permission **Mail.Send**, and grant
   admin consent.
2. **Limit it to one mailbox** (strongly recommended). Without a limit,
   Mail.Send lets the app send as anyone. In Exchange Online PowerShell:

   ```powershell
   New-ApplicationAccessPolicy -AppId <client id> -PolicyScopeGroupId rbaclens@yourdomain `
     -AccessRight RestrictAccess -Description "RBAC Lens sends only as this mailbox"
   ```

   Exchange's newer *RBAC for Applications* does the same.
3. **Settings** in `.env.prod`:

   ```
   NOTIFICATION_CHANNEL=email
   NOTIFICATION_SENDER=rbaclens@yourdomain
   NOTIFICATION_ADMIN_EMAIL=azure-admins@yourdomain
   PUBLIC_URL=http://localhost
   ```

4. **Redeploy** (`make up prod`), then use **Send a test message** on
   Settings › Notifications.

How delivery works:
- **Timing:** the scheduler sends waiting messages every minute.
- **Addresses:** each person's address comes from Entra ID (`mail`, else
  the user principal name).
- **Undeliverable:** a message with no address (no admin address, or a
  deleted user) is closed with the reason and not retried.
- **Failures:** if Graph refuses a message (wrong permission, unknown
  mailbox), the error shows on the page, delivery pauses for 15 minutes,
  and it is tried again.
- **Old messages:** anything older than 7 days when sending starts is
  closed, not sent late.

## Corporate TLS / proxy CA

If you run on a network where outbound HTTPS to `management.azure.com`
or `graph.microsoft.com` is intercepted by a proxy with a self-signed
root CA, the backend will fail to verify the cert and every Graph /
Azure SDK call will error out (look for `SSL: CERTIFICATE_VERIFY_FAILED`
in the logs).

The backend respects `REQUESTS_CA_BUNDLE`. Point it at the proxy's
PEM-encoded root CA, and both the Azure SDK (`SubscriptionClient`,
`AuthorizationManagementClient`, `ManagementGroupsAPI`,
`ResourceManagementClient`) and the Graph resolver will verify
against that file. `SSL_CERT_FILE` is honored as a fallback.

The path must be valid inside the backend container, so you also
need to bind-mount the host file:

```yaml
# docker-compose.test.yml / docker-compose.prod.yml
backend:
  environment:
    REQUESTS_CA_BUNDLE: /etc/ssl/corp-ca.pem
  volumes:
    - /path/on/host/corp-ca.pem:/etc/ssl/corp-ca.pem:ro
```

`REQUESTS_CA_BUNDLE` can also go in `.env.prod` (the base compose file
passes it on); the mount still goes in the overlay. Run `make restart prod`
after changing either side: Docker only applies
env changes when a container is recreated (`make env-check prod` shows
what's pending).

## First-time deploy

```bash
# On the host, from the repo:
make env prod         # creates .env.prod + ENCRYPTION_KEY
# Edit .env.prod: CORS_ORIGINS and ARM_* (or save the credentials on the Settings page)
# (POSTGRES_PASSWORD is generated)
make up prod
make logs prod SERVICE=backend
```

The backend's `entrypoint.sh` runs `alembic upgrade head` before uvicorn
starts, so the schema is current on every container start.

## Upgrading

```bash
git pull              # main = the latest release (vX.Y.Z tags)
make backup prod
make restart prod     # rebuilds, recreates, migrations run on start
```

### Published images

Each release is also on Docker Hub, for linux/amd64 and linux/arm64:
`gclntm/rbaclens-backend` and `gclntm/rbaclens-frontend`, tagged with the
version and `latest`. [`compose.yaml`](../compose.yaml) at the repository
root runs them on their own, without this checkout: the database, the
backend and the frontend, on `http://localhost:8080`. Its header has the
four commands (download it, write `.env`, `docker compose up -d`), the
optional settings and how to upgrade and back up. It leaves out what the
`make` stack adds: automatic backups, Mailpit, the test stack.

## AI wording (optional)

Off by default. When an admin turns it on (Settings › Advice › AI wording),
grant and identity pages offer "Explain in plain words": Claude rewrites the
playbook's advice for a non-specialist. The playbook still decides every
action, and the assessment report never uses it.

- **Key:** an Anthropic API key pasted in Settings is stored encrypted with
  `ENCRYPTION_KEY` and never shown again. Without a stored key,
  `ANTHROPIC_API_KEY` from the backend's environment is used (add it to the
  overlay, see [Backend settings](#backend-settings)).
- **Model:** Claude Opus 5.5 by default, or Sonnet 5.5 or Haiku 5.5.
  Opus and Sonnet requests opt into Anthropic's server-side fallback, so a
  declined request is retried on another model.
- **What is sent:** the role, the place, what the role allows, how it is
  given, day counts and the advice's own text. Never a person's name, UPN or
  object id. Each page shows the exact facts under "What is sent".
- **Cost:** texts are cached in `ai_texts` by a hash of the model, prompt
  version and facts, so the same facts are never sent twice.
- **Network:** the backend calls `api.anthropic.com` over HTTPS, through the
  corporate CA bundle if one is set.

## Backups

The Postgres data lives in the `rbaclens-prod_db_prod_data` volume.
`make backup prod` runs `pg_dump -Fc` inside the db container (consistent
while the app runs) and writes `backups/prod-<timestamp>.dump`:

```bash
make backup prod
make restore prod BACKUP=backups/prod-20260101-020000.dump   # asks you to type 'prod'
```

`restore` stops the backend and frontend, empties the schema and loads the
dump in one transaction (if it fails, nothing changes), then starts them.

### Automatic backups

The prod stack runs a `backup` service (`rbaclens-backup-prod`,
`scripts/backup-loop.sh`). Once a day, from `BACKUP_HOUR` (default 2,
container time, so `TZ` applies), it writes
`backups/prod-YYYYMMDD-HHMMSS-auto.dump`, checks the file reads back
(`pg_restore -l`), and keeps the newest `BACKUP_KEEP` (default 14)
automatic dumps. Files from `make backup prod` are never removed. If the
machine was asleep or off at that hour, the day's backup is taken as soon
as it is back. Dumps are mode 600.

```bash
make logs prod SERVICE=backup                                      # what it did
docker compose -p rbaclens-prod exec backup backup-loop.sh --now   # one now
```

A failed dump is logged as `FAILED` and tried again 30 minutes later.
Copy `backups/` somewhere off the machine for real safety.

Secrets saved in the app are encrypted with `ENCRYPTION_KEY`, so a dump
alone doesn't reveal them; everything else in it is readable.

## Who can reach the app

By default `make up prod` publishes the site on `127.0.0.1:80`: only this
machine can open it. To open it to the network, set `BIND_ADDRESS` in
`.env.prod` and run `make restart prod`:

```bash
BIND_ADDRESS=0.0.0.0        # every network interface
BIND_ADDRESS=192.168.1.20   # or one of the machine's addresses
```

Turn sign-in on first ([auth.md](auth.md)). With sign-in off, anyone who
reaches the app can read and change everything in it, as an admin. The
app says so in three places:

- **`make health prod`** (also printed by `make up`) ends with a line such
  as `sign-in: off, the app answers on this machine only`, or a `!!`
  warning when sign-in is off and `BIND_ADDRESS` opens the app to the
  network.
- **The backend log** carries a `WARNING` at every start while sign-in is
  off, and when someone switches it off.
- **The page** shows a red banner on every screen when sign-in is off and
  it was opened by a network address (not `localhost`). "Hide for now"
  hides it until the browser tab is closed.

The test stack always listens on 127.0.0.1.

## Reverse proxy / TLS

The prod compose binds the frontend container's nginx on port 80 only
(on `BIND_ADDRESS`, see above).
Front it with whatever your platform uses for TLS (Caddy, Traefik,
nginx, an Azure App Gateway, …). The backend doesn't need to be
reachable from outside the host: the frontend nginx proxies `/api/*`
to it over the compose network. CORS is still enforced even with a
reverse proxy in front.

## Security headers and host names

The frontend's nginx (`frontend/nginx/default.conf.template`) sends these
headers on every response, the API's included:

- **Content-Security-Policy:** scripts, styles and fonts only from the
  site itself (inline styles allowed); the fonts are bundled from npm
  (`@fontsource`), so pages contact no font service. Sign-in may talk to
  `login.microsoftonline.com`. No framing (`frame-ancestors 'none'`), no
  plugins, forms post only to the site.
- **Cache-Control:** `/assets/*` (file names carry a hash) is kept a year;
  pages and the API are `no-cache`, so browsers load a new version right
  after a deploy. The side menu shows the running version next to the name
  and offers a reload if the page is older.
- **Others:** `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`,
  `Cross-Origin-Opener-Policy: same-origin`, and no nginx version in
  `Server`.

**Host names.** The site answers only to `localhost`, `127.0.0.1`, `[::1]`,
`*.local` names and IP addresses, so it stays reachable by the Mac's
address on the local network. Requests for any other host name get no
response, which stops a DNS name that suddenly points at this host from
reaching the app. To add names, set `EXTRA_SERVER_NAMES` (space-separated)
in `.env.prod`.

The backend checks the host name too (`app/core/request_guard.py`, same
rule, same `EXTRA_SERVER_NAMES`), so the test stack's API port is covered
as well: a request naming another host gets `400`.

**Requests from other websites.** A browser sends a request to this app
when any page asks it to, including a page of another site the operator
has open. The backend therefore refuses every state-changing request
(`POST`, `PUT`, `PATCH`, `DELETE`) whose `Origin` is not the address the
app was opened on (or one listed in `CORS_ORIGINS`), or that the browser
marks as cross-site: `403`, a `WARNING` in the log, and a row in the audit
log. Requests without an `Origin` (curl, scripts) are not affected. Behind
your own reverse proxy, pass the browser's `Host` header through unchanged
(`proxy_set_header Host $http_host;` in nginx), or list the public address
in `CORS_ORIGINS`.

Add `Strict-Transport-Security` once TLS is in front (see above).

## Keeping images up to date

```bash
make scan prod    # Trivy: lists fixable HIGH/CRITICAL, fails on a fixable CRITICAL
make build prod   # pull fresh base and service images, rebuild without cache
make up prod
```

The images also apply their distribution's security updates at build time
(Debian for the backend, Alpine for the frontend), and the backend image
drops pip once its packages are installed. Run `make scan` monthly, and rebuild when it
reports something. `make backup prod` first, as for any redeploy.

Trivy itself is pinned by version and digest (`TRIVY_IMAGE` in the
Makefile). If it can't scan an image (no network, Docker error), `make scan`
says so and exits 2: a scan that did not happen never reads as "nothing
found".

### Container hardening

- **No root:** the backend runs as `app` (uid 1000), the frontend as
  nginx's own user (uid 101, `nginx-unprivileged`, listening on 8080 inside
  the container; compose publishes it on the host's port 80), and the
  backup job as `postgres` (uid 70). Postgres starts as root only to drop
  to its own user. On a Linux host, `./backups` must be writable by uid 70
  (`sudo chown 70:70 backups`); Docker Desktop needs nothing.
- **`no-new-privileges`** on every container: no process can gain rights
  through a setuid binary.
- **No default password in prod:** the prod stack refuses to start without
  `POSTGRES_PASSWORD` (`make up prod` generates it).
- **Logs rotate:** 5 files of 10 MB per container (`x-logging` in
  `docker/docker-compose.yml`).
- **Exact versions:** builds install hash-checked lockfiles ([development.md](development.md#lockfiles)).

## Azure identity (least privilege)

RBAC Lens only reads from Azure. It lists management groups,
subscriptions, role assignments, role definitions and PIM eligibilities,
reads the Activity Log, and asks Microsoft Graph for users, groups (with
their transitive members) and service principals. Email through Graph
(optional) is its only write.

The stacks sign in as a read-only service principal, by default named
**`sp-rbaclens-reader`**, whose `ARM_*` go in the env file (or on the
Settings page). Give it:

- **Reader** on the management group to review (for example
  `mg-contoso`), or on the Tenant Root Group for everything;
- **Management Group Reader** on the Tenant Root Group, for the tree above;
- the Entra role **Directory Readers**, for users, groups and apps in
  Microsoft Graph.

These roles also cover the Activity Log of the management groups (read
over `…/managementGroups/<id>/providers/microsoft.insights/eventtypes/
management/values`), the Tenant Root Group included.

**Who changed a group (optional).** Adding someone to a group is a change
in Entra ID, which the Activity Log never sees. To say who did it, RBAC Lens
reads the Entra ID audit log, which needs one more permission: the Microsoft
Graph *application* permission **AuditLog.Read.All** (read-only), with admin
consent. In the Entra admin center, open the app registration of
`sp-rbaclens-reader`, then **API permissions → Add a permission → Microsoft
Graph → Application permissions → AuditLog.Read.All → Grant admin consent**.
RBAC Lens tries the log after each check (or with **Check now** under
Activity Log coverage on Data & schedules) and adds its hourly collection
once it can read it. Entra keeps the log 7 days on Entra ID Free and
30 days on P1/P2, so the first collection reads at most that. If Graph
refuses it because of the tenant's licence, the page says so. Without the
permission, nothing changes: the Changes page says when a member was first
seen, not who added them.

It cannot change anything in Azure. Don't reuse an identity that can
write (a deployment pipeline's, for example).

- **Email (optional):** add `Mail.Send` only with
  `NOTIFICATION_CHANNEL=email`, limited to the sender mailbox (see
  [Real email through Microsoft Graph](#real-email-through-microsoft-graph)).
- **Secret:** note when it expires; a check that fails on
  authentication is the sign. Renew it with `az ad app credential reset`,
  put the new value in `ARM_CLIENT_SECRET` in `.env.prod`, then
  `make restart prod`.
- **Another identity:** change the three `ARM_*` values in the env file,
  then `make restart <env>`; `make whoami <env>` shows which one is set.

### Setting up the service identity

What `sp-rbaclens-reader` needs, in one table. These are three kinds of
permission, each set in a different place:

| Permission | Kind | Where it is set | Needed for |
|---|---|---|---|
| **Reader** | Azure role | On the management group to review, or the Tenant Root Group | Required: role assignments, PIM, the Activity Log |
| **Management Group Reader** | Azure role | On the Tenant Root Group | Required: the tree of management groups above |
| **Directory Readers** | Entra role | Entra ID → Roles and administrators | Recommended: names, and the members of groups |
| **AuditLog.Read.All** | Microsoft Graph *Application* permission, admin consent | The app registration → API permissions | Optional: who added or removed a group member |
| **Mail.Send** | Microsoft Graph *Application* permission, admin consent | The app registration → API permissions | Optional: real email ([below](#real-email-through-microsoft-graph)) |

**Application, not Delegated.** A Microsoft Graph permission comes in two
types. *Delegated* acts for a person who is signed in; *Application* acts as
the app itself. RBAC Lens signs in as the app, with nobody signed in, so only
**Application** permissions reach it. A Delegated one shows "Granted" on the
API permissions page and still does nothing: the token's `roles` stay
empty, and Graph answers `Authentication_MSGraphPermissionMissing`. Check
the **Type** column says *Application*.

**Who can do it:** creating the app registration needs Application
Developer (or any user, when the tenant allows it). Giving it the Azure
roles needs Owner or User Access Administrator on the Tenant Root Group;
the Entra role and the admin consent need Privileged Role Administrator
(or Global Administrator).

**With the Azure CLI**, signed in (`az login`) as someone with those rights.
The Tenant Root Group's id is the tenant id.

```bash
TENANT_ID=$(az account show --query tenantId -o tsv)
ROOT="/providers/Microsoft.Management/managementGroups/$TENANT_ID"

# 1. The app, its service principal, a secret valid one year, and Reader on everything.
#    Prints appId, password and tenant: copy them into the env file (below), then clear
#    the terminal. The secret is shown only once.
az ad sp create-for-rbac --name sp-rbaclens-reader --role Reader --scopes "$ROOT" --years 1

APP_ID=$(az ad app list --display-name sp-rbaclens-reader --query "[0].appId" -o tsv)
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)

# 2. Management Group Reader on the Tenant Root Group.
az role assignment create --assignee "$APP_ID" --role "Management Group Reader" --scope "$ROOT"

# 3. The Entra role Directory Readers.
ROLE_ID=$(az rest --method GET \
  --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions?\$filter=displayName eq 'Directory Readers'" \
  --query "value[0].id" -o tsv)
az rest --method POST --url "https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignments" \
  --body "{\"principalId\": \"$SP_ID\", \"roleDefinitionId\": \"$ROLE_ID\", \"directoryScopeId\": \"/\"}"

# 4. Optional: AuditLog.Read.All as an Application permission ("=Role"; "=Scope" would be
#    Delegated), then admin consent.
GRAPH=00000003-0000-0000-c000-000000000000
AUDIT=$(az ad sp show --id "$GRAPH" --query "appRoles[?value=='AuditLog.Read.All'].id | [0]" -o tsv)
az ad app permission add --id "$APP_ID" --api "$GRAPH" --api-permissions "$AUDIT=Role"
az ad app permission admin-consent --id "$APP_ID"
```

**In the portal** instead:

1. **Entra admin center → App registrations → New registration**: name it
   `sp-rbaclens-reader`, single tenant, no redirect URI. Note the
   *Application (client) ID* and the *Directory (tenant) ID*.
2. **Certificates & secrets → New client secret**: copy the *Value* (shown
   once), and note when it expires.
3. **Azure portal → Management groups → Tenant Root Group → Access control
   (IAM) → Add role assignment**: **Reader**, then **Management Group
   Reader**, each to `sp-rbaclens-reader`. To review only part of the
   tenant, give Reader on that management group instead.
4. **Entra admin center → Roles and administrators → Directory Readers →
   Add assignments**: `sp-rbaclens-reader`.
5. Optional: **App registrations → sp-rbaclens-reader → API permissions →
   Add a permission → Microsoft Graph → Application permissions →
   AuditLog.Read.All**, then **Grant admin consent**.

**Then**, in the env file of the stack (`.env.prod`, or `.env.test`):

```bash
ARM_CLIENT_ID=<application (client) id>
ARM_CLIENT_SECRET=<secret value>
ARM_TENANT_ID=<tenant id>
```

`make restart prod`, and `make whoami prod` shows which identity is set.
Or leave them empty and save them on **Settings → Credentials**. Role
assignments can take a few minutes to apply. The first check shows what the
identity can read; with AuditLog.Read.All, Data & schedules says *Entra ID
audit log: read* after that check, or straight away with **Check now**.

## Healthchecks

`db` has a `pg_isready` healthcheck in compose. `GET /api/health` returns
`{status: ok|degraded, version, stage, db: ok|unreachable}`; `make health
<env>` prints it with the containers' state and the sign-in line.

## Observability

Logs are structured JSON via `python-json-logger`. Mount stdout into
your logging stack of choice (Loki, Cloud Logs, Splunk). They carry no
request addresses with tenant detail: the HTTP client used for Graph and
the Azure SDK log every request's address at INFO, so they are held at
WARNING unless `LOG_LEVEL=DEBUG`, and the access log drops query strings
(searches, caller names). AI wording writes at most `AI_DAILY_LIMIT` new
texts a day (default 200; 0 = no limit). There are no metrics yet.

## Authentication / authorization

Sign-in with Microsoft Entra ID is optional and off by default; see
[auth.md](auth.md). A wrong setting that locks everyone out is undone with
`make auth-off prod` on the host. Exposed beyond a private network, also
put the app behind your organisation's SSO or proxy auth (Easy Auth,
oauth2-proxy, Cloudflare Access…), which gates everything before it reaches
the app.
