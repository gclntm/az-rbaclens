# Authentication

This app supports optional sign-in with **Microsoft Entra ID** (Azure
AD). When enabled, every API call requires a valid access token issued
for the configured app registration; the SPA uses MSAL.js to acquire
the token and the FastAPI backend validates it against the tenant's
JWKS.

Auth is **off by default** — the app keeps working unauthenticated
until you flip the toggle in Settings → Authentication. Useful for
dev / test, less useful in shared production: see the warning at the
bottom of this page.

## How the pieces fit together

```
Browser (SPA) ── MSAL redirect ──► login.microsoftonline.com
        ▲                                    │
        │ access token                       │
        ▼                                    ▼
   GET /api/auth/config (public)      issues access token
        │
        ▼
   Bearer <token> on every API call
        │
        ▼
   FastAPI ── verify signature ──► JWKS endpoint (tenant)
        │   ── check iss / aud / exp / groups
        ▼
   route handler runs (or 401)
```

* `/api/auth/config` is the only auth-related endpoint that stays
  public — the SPA has to know whether to log in *before* it can log
  in.
* `/api/health` also stays public so orchestrators can liveness-probe.
* `/api/settings/auth` (the sign-in settings) is open only while sign-in
  is off, so it can be set up without signing in. Once sign-in is on, it
  is admins only, like the rest of `/api/settings/*`.

## Configure step-by-step

### 1. Register the app in Entra ID

1. Entra admin center → **App registrations** → **New registration**.
2. **Account types**: choose what your org requires (typically single
   tenant).
3. **Redirect URI**: select **Single-page application (SPA)** and
   enter the exact origin of the deployment, e.g.
   `https://rbaclens.example.com` (no trailing slash, no path).
4. Save and copy:
   * **Application (client) ID**
   * **Directory (tenant) ID**

### 2. Add the API permission

Under **API permissions** → **Add a permission** →
**Microsoft Graph** → **Delegated** → `User.Read`.

That's enough to issue tokens. The backend only validates the
signature + audience; it does **not** call Graph on the user's
behalf.

### 3. (Optional) Restrict by group

If you want only certain Entra groups to be allowed:

1. **Token configuration** → **Add groups claim**.
2. Pick **Group ID**; under **Access token** tick **Group ID**.
3. Save. The next access token the SPA acquires will contain a
   `groups` claim with the user's Entra group object IDs.
4. In **Settings → Authentication** in this app:
   * **Allowed group IDs** — gates the whole app. The user must be in
     at least one of these groups or any API call returns 401.
   * **Admin group IDs** — gates the admin endpoints (`/api/settings/*`,
     `/api/audit/*`, `/api/notifications*`, and changes such as setting
     who is responsible, opening reviews or saving an assessment; see
     [api.md](api.md#who-may-call-what)). Members get the full UI;
     non-members see their own places and access. Required: sign-in
     can't be turned on without one, and with none set nobody is an
     admin.

All ids (tenant, client, groups) are GUIDs; anything else is refused.

> Note: if a user is a member of more than ~150 groups, Entra
> returns an "overage claim" instead of inline group IDs. The
> validator currently rejects those — a follow-up could resolve the
> overage via Graph. File a bug if you hit it.

### 4. Turn it on

In the app: **Settings → Authentication**.

* Tick **Require sign-in for every API call**.
* Paste the tenant ID, the client ID and at least one admin group ID.
* (Optional) paste allowed group IDs.
* **Save**.
* Reload the page — the SPA will redirect to `login.microsoftonline.com`,
  and on return every API call carries `Authorization: Bearer <jwt>`.

## What people who are not admins see

With sign-in on and **Admin group IDs** set, anyone signed in outside
those groups sees a narrower app (`app/core/visibility.py`):

- **Places:** the places where they are set as responsible (Settings ›
  Who is responsible), and everything inside them, even parts someone else
  reviews.
- **Grants:** everything that applies to those places, including grants
  given above (who can act on their place), and the identities holding
  them. On someone else's page, only the grants that apply to their
  places.
- **Their own access:** directly or through their groups, with their own
  "What they did".
- **Reviews:** the ones they do, or that cover their places. They decide
  only in their own.
- **To do and Report:** the to-dos of those reviews and places (they can
  dismiss their own), and the report of one of their places; the
  whole-tenant report is for admins.
- **Not shown:** the tenant-wide Home figures (they get a personal Home),
  the Activity Log of other people, snapshots, jobs, schedules, settings,
  who holds a role across the tenant, and decisions on findings (they
  decide in their review).

A single guard on every signed-in route refuses anything not on its
allow-list (403), so a new endpoint is closed to these users until it is
added there. With **Admin group IDs** empty nobody is an admin, which is
why sign-in can't be turned on without one.

## Token specifics

* Algorithm: `RS256` (Entra's signing algorithm).
* Audience: accepts both `<client_id>` and `api://<client_id>` —
  matching MSAL's default behaviour in browser flows.
* Issuer: `https://login.microsoftonline.com/<tenant_id>/v2.0`.
* Clock drift: 60 seconds either way.
* Issued to this app: `azp` (or `appid`) must be the client id, so a token
  another app obtained for this API is refused.
* A person's token only: a token an app obtained for itself (client
  credentials, no `scp` and no user name, or `idtyp: app`) is refused.
* Signing keys: fetched through the CA bundle like every outbound call, kept
  24 hours, and fetched early only for an unknown key id, at most once a
  minute.
* A refused token gets `401` with the same text whatever the reason
  ("sign-in token not accepted: sign in again"); the reason is in the
  backend log (`sign-in token refused on …`), never the token.

## Disabling auth

Untick the toggle in Settings → Authentication and **Save**. The
backend immediately stops requiring a token; the SPA stops trying to
acquire one on its next reload. No restart needed.

While sign-in is off, anyone who reaches the app can read and change
everything, as an admin. The app warns about it in the backend log, in
`make health`, and with a banner on pages opened by a network address; by
default the prod stack only listens on this machine. See
[deployment.md](deployment.md#who-can-reach-the-app).

## Locked out? Turn sign-in off from the host

Once sign-in is on, only admins can change the sign-in settings. A wrong
tenant, client id or admin group can therefore lock everyone out. The way
back in needs shell access to the host, not network access to the app:

```bash
make auth-off prod   # asks you to type the env name
```

It runs `backend/scripts/auth_off.py` in the backend container. That turns
sign-in off and keeps the tenant, client and group ids, so you can fix them
in Settings › Authentication and turn sign-in back on.

Sign-in can only be turned on with a tenant id, a client id and at least
one **Admin group ID**; without an admin group, every signed-in user of the
tenant would be an admin. App-level sign-in is not enough on its own
for an internet-exposed deployment; put TLS and, ideally, an auth proxy in
front (see [`docs/deployment.md`](deployment.md#reverse-proxy--tls)).

## Audit log

Every mutating call to `/api/*` (POST / PUT / PATCH / DELETE) records a
row in the `audit_events` table — `occurred_at`, `actor` (principal
GUID from the JWT, or `anonymous` when auth is off), `actor_name`
(display name / UPN when available), `client_ip` (the address the call
came from), HTTP method, path, and the response status. Read-only GETs
are *not* logged.

The address is the one nginx saw: nginx overwrites `X-Forwarded-For`
with it and the prod backend trusts that header (`FORWARDED_ALLOW_IPS`),
so a caller can't write another address into the log. On Docker Desktop
(macOS, Windows) every caller shows as the Docker gateway (`192.168.65.1`),
because Docker Desktop doesn't pass the source address into containers;
on a Linux host it is the real caller.

Browse the log under **Settings → Audit log** (admin-only). Useful
for:

* Confirming who saved or changed credentials
  (`PUT /api/settings/credentials`).
* Spotting brute-force or misconfigured clients
  (a run of 401/403 entries from the same actor).
* Investigating "did this scan get deleted on purpose?"
  (`DELETE /api/iam-access/scans/<id>`).

Body content is **not** captured — payloads can contain secrets. For
"the secret changed at this time" you can infer it from the existence
of the `PUT /api/settings/credentials` row.

## Troubleshooting

### "Could not load auth config: 401"

The SPA hits `/api/auth/config` before MSAL initializes. If it's
returning 401 something is gating the public endpoint — usually a
reverse proxy not configured to allow it. Make sure
`/api/auth/config` and `/api/health` are reachable without auth at
the proxy level.

### "sign-in token not accepted"

The page shows this for every refused token; the reason is in the backend
log (`make logs <env> SERVICE=backend`, look for `sign-in token refused`):

- **"Signature verification failed"**: the token was issued for a
  different tenant than the one configured. Re-check the tenant ID.
- **"Invalid audience"**: the token's `aud` is not `<client_id>` or
  `api://<client_id>`. The page asks for `${client_id}/.default`: keep the
  API permissions on the same app registration so the audience matches.
- **"token issued to another app"**: the token was obtained by another app
  registration than the one configured.
- **"app-only token"**: an app called the API with its own token; a person
  must sign in.

### "the token's groups claim has none of the allowed groups"

Either the user isn't in the allowed groups, or the app registration
isn't emitting the `groups` claim. Re-check **Token configuration**
in Entra and `Allowed group IDs` in Settings.
