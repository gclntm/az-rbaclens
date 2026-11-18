# Changelog

Release notes for RBAC Lens. New notes go under the unversioned
`## YYYY-MM-DD` heading at the top (add one if the top section already
carries a version). `make release V=X.Y.Z` turns that heading into
`## [X.Y.Z] - YYYY-MM-DD`, bumps the version and tags `vX.Y.Z` — see
[README-MAKE.md](README-MAKE.md) and `scripts/release.sh`.

## 2026-11-18

- **Entra ID is a place of its own**, at the top of Places: who holds which
  Entra role, standing, eligible through PIM or activated, directly or
  through a group. Some Entra roles lead straight into Azure access (a
  Global Administrator can make themselves User Access Administrator over
  every subscription), so they are on the same five levels as Azure roles,
  by what their permissions allow. The daily check of the Tenant Root Group
  reads them; the Directory Readers role the identity already has is
  enough. See [iam-access.md](iam-access.md#entra-id).
- **Needs a look on Entra roles:** an admin role held all the time instead
  of through PIM (break-glass identities left out), an app holding a
  powerful role, more than four Global Administrators, a powerful role
  through a group.
- **A powerful role held by a partner's group from another tenant**
  (delegated admin access, GDAP), Azure or Entra, needs a look: its members
  can't be seen from your tenant. Such a group is no longer reported as an
  identity that no longer exists, nor its access as unused. The advice suggests PIM; the to-dos remove an Entra role
  through Microsoft Graph.
- **The Changes page says who gave or removed an Entra role**, directly or
  through PIM, from the Entra ID audit log, and an identity's page lists the
  Entra roles it activated through PIM.
- **A PIM activation is no longer a change.** A check that ran while someone
  had activated a role, Entra or Azure, reported it as given, then removed
  the next day.
- Entra ID gets a responsible person and a monthly review; the report's
  privileged-access control counts its standing admin roles.

## [0.33.0] - 2026-11-18

- **The Changes page can say who added or removed a group member**, and
  exactly when. Joining a group is a change in Entra ID, which the Azure
  Activity Log never sees, so RBAC Lens can now read the Entra ID audit log
  (through Microsoft Graph, every hour) and keep its "Add member to group" and
  "Remove member from group" entries. It is optional: the identity needs the
  Graph application permission AuditLog.Read.All (read-only). RBAC Lens tries
  the log after each check and starts collecting once it can read it; Data &
  schedules says whether it can, and why not, with **Check now**. A member
  added to a group nested inside the one that holds access is matched too;
  a whole group nested into it still shows as "first seen". A change Entra
  records as made by an app (Teams or Outlook for Microsoft 365 groups,
  access packages, PIM, the AD sync) names the app. See
  [iam-access.md](iam-access.md#entra-id-audit-log).
- **Setting up the service identity, step by step**
  ([deployment.md](deployment.md#setting-up-the-service-identity)): every
  permission it needs in one table, Azure CLI commands and portal steps,
  and why a Microsoft Graph permission must be of the *Application* type.
  The README's quick start links to it.
- **Plans are no longer published.** Working plans are saved as
  `docs/plan-<topic>.md`, which git ignores.

## [0.32.2] - 2026-11-16

- **The README has a "First steps" section**, from a fresh install to the
  first analysis: what a check, an Activity Log collection and a schedule
  are, the identity to give it, the first check, the schedules it adds by
  itself, which pages to start the analysis from, and how to set up the
  monthly review.

## [0.32.1] - 2026-11-14

- **The frontend image installs Alpine's security updates** when it is
  built, as the backend does with Debian's: the nginx base image carried
  fixable vulnerabilities (one critical, in brotli).
- **The backend image no longer contains pip**, which is only needed to
  build it. pip bundles an old copy of setuptools' `pkg_resources` that
  scanners report as setuptools 70.3.0 (CVE-2025-47273). Both images now
  scan without a fixable high or critical vulnerability.
- **A time-dependent test is fixed:** the token-expiry test computed its
  times when the tests were collected, so it failed on slow machines.

## [0.32.0] - 2026-11-14

First public release. RBAC Lens was developed and used privately before
this (versions 0.1 to 0.31); this file starts here.

### What it does

- **Checks any Azure RBAC scope** (management group, subscription,
  resource group, resource), on demand or daily, and compares each check
  with the one before: who got or lost access, and who gave it.
- **Shows every place, every person or app and every grant in plain
  words:** what it allows, where, since when, standing or on request
  (PIM), with conditions (ABAC) explained.
- **Reads the Activity Log** to show which permissions are actually used,
  and keeps a feed of access changes.
- **Advice on every grant, monthly reviews** by a responsible person per
  place, a to-do list after each review, and an assessment report
  mapped to ISO/IEC 27001:2022 (print to PDF, CSV annex).
- **Optional Entra ID sign-in** with an admin group; signed-in users see
  only their places. **Optional AI wording** of the advice (Anthropic).
- **Only reads Azure:** a read-only service principal is enough.

### New since the last private version

- **Licence: Apache 2.0** (`LICENSE`, `NOTICE`); the Untitled UI
  components it includes stay under MIT.
- **Azure credentials live in the env files** (`ARM_*` in
  `.env.<env>`) or on the Settings page; `make whoami <env>` shows which
  identity is set. See [development.md](development.md#azure-credentials).
- **Images on Docker Hub** for linux/amd64 and linux/arm64
  (`gclntm/rbaclens-backend`, `gclntm/rbaclens-frontend`), published by
  the release workflow after a vulnerability scan. `compose.yaml` runs
  them on their own, without cloning: see
  [deployment.md](deployment.md#published-images).
- **CI:** `make check` runs on GitHub for every push and pull request,
  and again before a release publishes anything. Dependabot proposes
  weekly updates of the npm packages, base images and Actions.
- **A leak check for contributors** (`make public-check`, `make hooks`):
  see [development.md](development.md#keeping-private-data-out-of-the-repository).
  `make release` checks the commit email first and pushes `dev`, `main`
  and the tag atomically.
