# Changelog

Release notes for RBAC Lens. New notes go under the unversioned
`## YYYY-MM-DD` heading at the top (add one if the top section already
carries a version). `make release V=X.Y.Z` turns that heading into
`## [X.Y.Z] - YYYY-MM-DD`, bumps the version and tags `vX.Y.Z` — see
[README-MAKE.md](README-MAKE.md) and `scripts/release.sh`.

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
  place, a to-do list after each review, and an **assessment report**
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
