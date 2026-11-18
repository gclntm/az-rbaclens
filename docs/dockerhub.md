# RBAC Lens

**Who can do what, where, and since when in Azure**, in plain words. RBAC
Lens checks any Azure RBAC scope (management group, subscription, resource
group, resource), daily or on demand, and shows every place, every person
or app and every grant: what it allows, where, since when, who gave it,
standing or on request (PIM), with conditions (ABAC) explained. It compares
each check with the one before, reads the Activity Log to show which
permissions are really used, gives advice on every grant, runs monthly
reviews by a responsible person per place, and prints an assessment report
mapped to ISO/IEC 27001:2022.

It only reads Azure: a read-only service principal is enough.

Source, documentation and issues:
[github.com/gclntm/az-rbaclens](https://github.com/gclntm/az-rbaclens)

## The two images

| Image | What it is |
|-------|------------|
| [`gclntm/rbaclens-backend`](https://hub.docker.com/r/gclntm/rbaclens-backend) | The API (Python, FastAPI): talks to Azure and Microsoft Graph, stores in PostgreSQL, migrates the database on start |
| [`gclntm/rbaclens-frontend`](https://hub.docker.com/r/gclntm/rbaclens-frontend) | The web interface (nginx, unprivileged), proxying `/api` to the backend |

They run together, with PostgreSQL 18. Tags: the version (`0.32.0`) and
`latest`, for linux/amd64 and linux/arm64, each with an SBOM and
provenance. Every release is scanned with Trivy before it is published.

## Run it

One file, no clone, no build:

```bash
mkdir rbaclens && cd rbaclens
curl -fsSLO https://raw.githubusercontent.com/gclntm/az-rbaclens/main/compose.yaml
printf 'POSTGRES_PASSWORD=%s\nENCRYPTION_KEY=%s\n' \
  "$(openssl rand -hex 24)" "$(openssl rand -base64 32 | tr '+/' '-_')" > .env
docker compose up -d          # then open http://localhost:8080
```

Then save the Azure credentials on **Settings → Credentials**, or add
`ARM_CLIENT_ID`, `ARM_CLIENT_SECRET` and `ARM_TENANT_ID` to `.env` and run
`docker compose up -d` again. The service principal needs **Reader** on
the scopes to check, **Management Group Reader** on the Tenant Root Group,
and the Entra role **Directory Readers**. How to create it, with the Azure
CLI or in the portal:
[Setting up the service identity](https://github.com/gclntm/az-rbaclens/blob/main/docs/deployment.md#setting-up-the-service-identity).

Keep `.env`: `ENCRYPTION_KEY` decrypts the credentials saved in the app.

## Settings

Optional, in `.env`:

| Setting | Default | |
|---------|---------|---|
| `RBACLENS_VERSION` | `latest` | Pin a version, e.g. `0.32.0` |
| `RBACLENS_PORT` | `8080` | Port on the host |
| `BIND_ADDRESS` | `127.0.0.1` | `0.0.0.0` opens it to the network (turn sign-in on first) |
| `TZ` | UTC | Time zone of the daily check (06:00) |
| `EXTRA_SERVER_NAMES` | | Host names it answers to besides localhost |

Sign-in with Entra ID is off until you turn it on under **Settings →
Authentication**; until then anyone who reaches the app is an admin, which
is why it answers on this machine only by default.

## Upgrade and back up

```bash
docker compose exec -T db pg_dump -U rbaclens -Fc rbaclens > rbaclens.dump
# set RBACLENS_VERSION in .env to the new version, then:
docker compose up -d          # the database migrates on start
```

Nothing else to run: the next daily check and the next hourly collection
fill in what the new version adds.

Release notes:
[CHANGELOG](https://github.com/gclntm/az-rbaclens/blob/main/docs/CHANGELOG.md).
More (building from source, email notifications, automatic backups, a
reverse proxy):
[deployment guide](https://github.com/gclntm/az-rbaclens/blob/main/docs/deployment.md).

## Licence

[Apache License 2.0](https://github.com/gclntm/az-rbaclens/blob/main/LICENSE).
It includes Untitled UI React components (MIT).
