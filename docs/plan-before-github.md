# Plan: before the first push to GitHub

Written 2026-11-09 from a review of version 0.15.2: backend, UI, security,
the data pipeline (checks, Activity Log, schedules) and what a published
repository needs. Each item has a size: **S** up to an hour, **M** half a
day, **L** a day or more. Phases are in the order to do them; the push is
the last step of phase 5.

## What the review found, in short

- **Data pipeline:** works, but fails quietly in four ways: a check that
  loses its connection midway is still recorded as complete; a failed daily
  check waits 24 hours to retry; a restart leaves jobs blocked for an hour;
  and every hourly collection recomputes all findings, even with nothing new.
- **Activity Log:** it dwarfs everything else in the database, grants
  included. Most events are two automatic heartbeat operations, and a
  large share of all rows are "Started" duplicates of a later
  "Succeeded". Only a small fraction of the events come from people.
- **Schedules page:** shows frequency, next and last run, but a schedule
  can't be edited, "Pause" is one unconfirmed click beside "Run now", the
  page doesn't show its own warnings, and failures scroll out of the newest
  50 runs within hours.
- **Security:** no secret in the tree or in history. But a fresh
  `make up prod` gives an open admin console on every network interface,
  and a few sign-in checks fail open.
- **Publishing:** the tree and its history held details of the tenant
  the app was developed against, and there was no licence.

## Phase 1: checks, Activity Log and schedules

### 1a. Jobs that fail safely (backend)

| # | What | Size |
|---|---|---|
| 1 | A check that could not read some places is `partial`, not `done`: it doesn't take over those places, records no "removed" changes for them and closes no to-dos. Today a network cut mid-check shows grants as removed and closes to-dos for good. | M |
| 2 | A failed scheduled job retries after 15 min, 1 h, 4 h (capped at its interval), and the schedule keeps the error until a run succeeds. | M |
| 3 | At start, jobs left running by the previous process are marked "interrupted by restart" and their schedule runs again at once. | S |
| 4 | Recompute findings after a collection only when it brought new events, and at most once per minute for all collections together. | S–M |
| 5 | Each schedule keeps its own "collected up to" mark, so a manual run can't hide a gap; a manual run can't end in the future; subscription ids are validated and lowercased. | M |
| 6 | Collection inserts with `ON CONFLICT DO NOTHING` instead of loading 120 days of event ids into memory each hour. | S |
| 7 | The sweeper and the audit log write off the event loop, so a slow database doesn't freeze every request. | S |
| 8 | Old collection records are pruned (keep 30 days and the newest per subscription). | S |

### 1b. A smaller Activity Log

Keep full detail where it matters, counts elsewhere:

- **Kept in full:** everything done by a person, every change to access
  (role assignments, PIM, locks, policy), and every failure.
- **Kept as one row per day:** the same app doing the same operation on
  the same resource, with a count and first/last time.
- **Not stored:** "Started" rows when the outcome arrived, and the raw
  Azure record for rows kept as counts.

"Used in 90 days" and the changes feed read the same facts as before.
Expected size: a small fraction of today's. Includes a one-off migration of the
stored events, with a backup first. **L**

### 1c. Data & schedules page

- **Daily check as its own card:** the place by name (not a GUID), "Every
  day at 06:00 (UTC)", next and last run, an On/Off pill, Check
  now, Edit.
- **Activity Log collection as a table,** one row per subscription, sorted
  by name: repeats, state, last run with its result or error in plain
  words, next run. One main button; Edit, Pause and Delete in a row menu.
- **Edit a schedule:** frequency, hour, first-run look-back (the API
  already accepts them).
- **Pause asks first,** says what stops, confirms with a toast and Undo,
  and the row shows "Off since … by …" (two new columns on the schedule).
  A "Resume all" button when any are off.
- **The page shows its warnings,** and the summary cards say "paused"
  when that is the truth.
- **Recent runs** with filters (All, Checks, Collections, Failed), full
  error inline, duration, scheduled or manual.
- **Errors in plain words:** "Could not reach Azure (network or proxy)",
  "Sign-in to Azure failed (secret expired?)", "Not allowed to read …",
  with the technical text behind "Details".
- **"All checks" folded into this page;** the old snapshot page goes.

**L** in total; the pause confirmation, warnings and card text are **S**
and can go first.

## Phase 2: safe by default

| # | What | Size |
|---|---|---|
| 1 | `make up prod` listens on 127.0.0.1 unless `BIND_ADDRESS` says otherwise. (An install that must stay reachable from other machines sets `BIND_ADDRESS=0.0.0.0` in its `.env.prod`.) | S |
| 2 | With sign-in off: a warning in the log and in `make health`, and a full-width banner when the page is opened from another machine. | S |
| 3 | Reject state-changing requests that come from another website (Origin / Sec-Fetch-Site check), and check the Host header in the backend too. | S |
| 4 | Sign-in on: require a user token (not an app-only one), deny admin when no admin group is set, allow 60 s of clock drift, fixed error text for bad tokens, validate tenant and client ids as GUIDs, fetch signing keys through the CA bundle at most once a minute. | M |
| 5 | `/freshness` and the custom-role list no longer show tenant-wide detail to non-admins. | S |
| 6 | Logs: no Graph URLs or query strings at INFO; log rotation in compose. | S |
| 7 | Containers: nginx and the backup job not as root, `no-new-privileges`, no password fallback in prod, Trivy pinned by digest and its errors not hidden. | M |
| 8 | AI wording: say in Settings that place and role names leave the tenant; a daily call cap. | S |
| 9 | `scripts/azure-env.sh` no longer defaults to the write-capable identity. | S |

## Phase 3: UI

- **Won't-break basics (S each):** an error boundary and a not-found page;
  a reviewer's Home says "Could not load" instead of "No review waiting"
  when the API fails; error toasts stay until dismissed.
- **Readable (S):** muted text and dark-mode buttons below the WCAG AA
  contrast minimum get darker tokens.
- **Half-width windows (M):** the top bar keeps the version, the "reload"
  notice and the data warnings, and hints that the menu scrolls.
- **One vocabulary (M):** check, place, grant, people & apps, collection
  everywhere a user reads; the three older pages (all checks, compare two
  checks, Activity Log) restyled or folded in.
- **Needs a look (M):** the cross-tenant list also groups per grant, so
  counts agree across pages; one shared set of decision buttons; the
  reason for "Accept risk" asked inline, not in a browser prompt.
- **Drawers (M):** focus moves in, stays in and returns; rows that open
  details work from the keyboard.

## Phase 4: code health

| # | What | Size |
|---|---|---|
| 1 | Remove what nothing calls: 6 API endpoints, 5 client functions, unused CSS, the `dry_run` setting. This also brings `api/iam_access.py` near the 500-line rule. | M |
| 2 | Split `tools/iam_access.py` (worker, walk, persist, ARM helpers), `api/iam_access.py` (scans, role definitions) and `db/models.py` (a package). | M |
| 3 | One UTC datetime column type instead of 13 "make aware" helpers. | M |
| 4 | Tests on real Postgres in `make check`: run every migration, compare with the models, then a smoke subset. Today no test runs a migration. | M |
| 5 | Ruff with a real rule set and format check, part of `make check`; ESLint with the React hooks and accessibility rules. | M |
| 6 | First component tests: schedules page (pause), reviewer Home (error), review decisions. | M |
| 7 | Load heavy JSON columns only where needed; build the "current view" once per request and cache it between checks. | M |

## Phase 5: publish

Decisions: a private GitHub repository first, public after review;
licence Apache 2.0.

Status (2026-11-14): steps 1 and 4 done, and the licence is in; the rest
of step 2 remains. Step 3 has started: CI runs `make check` on every push,
and the release workflow publishes the images to Docker Hub and creates the
GitHub release, and Dependabot proposes weekly updates; the branch and tag
rules and CodeQL are documented
([development.md](development.md#branch-and-tag-rules)) and set on GitHub
when the repository goes public.

1. **Scrub the tree (M), done.** Invented values in code, tests and docs
   (`mg-contoso`); no tenant names, ids or measurements. A check keeps it
   that way: [development.md](development.md#keeping-private-data-out-of-the-repository).
2. **Make it usable by a stranger (M).** README: what it is, screenshots
   with invented data, the exact Azure roles needed, a quick start without
   `pass`, the security model, limits. `LICENSE` (done), `SECURITY.md`, a
   short `CONTRIBUTING.md`.
3. **GitHub side (M).** CI running `make check`, lint, Trivy and CodeQL;
   Dependabot for npm, Docker and Actions, and a weekly `make lock` PR;
   rules on `main`, `dev` and `v*` tags; secret scanning with push
   protection. `scripts/release.sh`: fetch first, push atomically, undo
   cleanly when refused, run `make check` before tagging.
4. **Publish from a fresh start (S), done.** The history starts with one
   commit made from the cleaned tree, authored with a GitHub no-reply
   address. Nothing older is published.

## Suggested order

1a → 1c (small parts) → 2 (items 1–3) → 1b → 1c (rest) → 2 (rest) → 3 → 4
→ 5. Phases 1 and 2 change behaviour and each deserves its own release;
3 and 4 can be released together; 5 ends with the push.
