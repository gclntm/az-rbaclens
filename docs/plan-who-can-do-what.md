# Plan — who can do what, where, and when

Status: **shipped** (phases 6–10, releases 0.3.0–0.8.0, 2026-11-08); next: [plan-advice-and-report.md](plan-advice-and-report.md). Mockup: made during design, not kept.

This plan follows [redesign-plan.md](redesign-plan.md). Phases 1–5 of that plan
shipped the data layer: registries, findings, review decisions, schedules,
capability levels, conditions and PIM. This plan changes what a person sees.
The app answers one question in plain words: **who can do what, where, and
since when**. Phases are numbered from 6 to follow the earlier plan. Tick the
boxes as they ship and add a line to `docs/CHANGELOG.md`.

---

## 1. Why

The app is built around how it works inside, not around what a reviewer
needs to know.

| Today | What the reviewer actually wants |
|---|---|
| "Snapshot #41", "Scans", "Jobs" in the nav | "Is this up to date?" One line is enough: *Access as of today 06:02*. |
| Findings, Coverage, Activity on separate pages | "What should I look at?" and "What changed?" |
| "Unused" shown as a finding | Azure never logs reads or data access. "No change in 90 days" is a fact to know, not a fault. |
| Assignments listed by role name | "Who can change access here? Who can get the keys?" |
| No one is responsible for a place | Monthly reviews need a reviewer for each place. |
| Grants show no date and no author | "Since when, and who gave it?" Azure records both (`createdOn` / `createdBy`). |

## 2. Decisions taken

Decided with the product owner on 2026-11-08.

1. **Audience:** platform admins, plus the people responsible for a place.
   Those people review the places they are responsible for themselves.
2. **Snapshots run automatically and stay mostly out of sight.** The whole
   tenant is checked every night. Pages show how fresh the data is
   ("as of …"); snapshots appear only in *Data & schedules*.
3. **"Unused" is evidence, not a finding.** It shows as "no change in N days
   (reads are not recorded)" next to the grant. It never counts as a problem
   on its own.
4. **Reviews are monthly.** The person responsible for a place reviews that
   place and the resource groups inside it.
5. **Responsible people are set in RBAC Lens.** The setting passes down the
   tree. An Azure `owner` tag that holds an email address becomes a
   *suggestion* that an admin accepts or ignores. The word is
   **Responsible**, never "owner", so it isn't confused with the Azure Owner role.
6. **"Ask" only marks the item for now.** The notification path (email or
   Teams) is prepared as an interface and an outbox, with no channel connected.
7. **RBAC Lens never changes Azure.** A "Remove" decision produces a
   ready-to-run command and an export. The next daily check confirms the grant is gone.

## 3. Words

The UI uses these words and no others. API and DB names stay as they are
unless a phase below says otherwise.

| UI word | Meaning | Replaces in the UI |
|---|---|---|
| **Place** | Management group, subscription, resource group or resource | Scope |
| **Grant** | One role given to one identity on one place | Role assignment |
| **Identity** | Person, group, app or managed identity | Principal |
| **What they can do** | The capability levels from `core/capabilities.py` (below) | Role risk, high-risk |
| **Daily check** | The nightly snapshot of the whole tenant | Snapshot, scan, job |
| **Change** | Access given or removed, with who and when | Drift, diff |
| **Needs a look** | Open findings that matter, as a short list on Home | Findings page |
| **Review** | The monthly decision round on a place: Keep / Remove / Ask | Mark reviewed / snooze |
| **Responsible** | The person who reviews a place | (new) |

Capability levels, worded for people:

| Level | Label | Colour |
|---|---|---|
| `access` | can change who has access | red `#b91c1c` |
| `keys` | can get keys and secrets | amber `#b45309` |
| `change` | can change resources | blue `#1d4ed8` |
| `data` | can read or write data | violet `#6d28d9` |
| `view` | can only look | slate `#475569` |

## 4. Screens

Each board in the mockup, the page it replaces, and what it reuses.

| Board | Route | Replaces | Reuses |
|---|---|---|---|
| Home | `/` | Overview | `core/overview.py`, `FindingCard`, registry stats |
| Places | `/places` | Scopes index, Snapshots | `ScopeTree`, `GET /scopes/tree` |
| Place · Who can do what | `/places/*` | Scope page Access + Who tabs | `ScopeWhoTab`, `whoUtils`, `GET /scopes/capabilities` |
| Place · Changes | `/places/*?tab=changes` | Scope History + Activity tabs | `diff_scans`, `GET /scopes/activity` |
| Identity | `/people/:id` | Principal page | `PrincipalPage` tabs, `GET /principals/{id}` |
| Grant | `/grants/:fingerprint` | Assignment drawer | `AssignmentDrawer`, `ConditionTag`, `PimTag` |
| Changes | `/changes` | Activity (Events) | new feed (phase 7) |
| Review | `/reviews`, `/reviews/:id` | Findings page | `review_decisions`, `FindingCard` |
| Data & schedules | `/admin/data` | Jobs, Snapshots list, manual pull form | `GET /jobs`, `/schedules` |
| Settings · Who is responsible | `/settings/responsible` | (new) | phase 8 |

Side menu: **Home** · *Access:* Places, People & apps, Changes, Reviews,
Role guide · *Admin:* Data & schedules, Settings. The footer shows how
fresh the data is ("Access as of …", "Activity up to … ago") and who is
signed in.

`/scopes/*`, `/principals/*`, `/findings`, `/iam-access/*`, `/events` and
`/jobs` redirect to their new homes for one release. ⌘K stays.

*Phase 10 as shipped:* a Settings › Notifications tab rather than a card on
*Data & schedules*; "due soon" is three days before the due date; a question
gets a reminder every 7 days while it stays open. `NOTIFICATION_CHANNEL`
is reserved for `email` / `teams`.

*Phase 9 as shipped (parts 1–2):* `/reviews` (list, "Only mine" with sign-in
on, count in the side menu) and `/reviews/:id` (cards, filters, copy
commands, export, finish); "Is this right?" on the Grant page. Rounds open
after every check rather than at 00:30. Part 3: non-admins are limited by
one allow-list guard (`app/core/visibility.py`); their places include
everything inside a place where they are set, even parts someone else
reviews, and grants given above that apply there.

*Phase 8 as shipped:* Settings › Who is responsible (`/settings/responsible`,
`?place=` opens a place's editor); the place header names the responsible
person and where the setting comes from; "yours" and "Places I'm
responsible for" use `GET /me` and appear only with sign-in on. A person is
entered by email or object id and checked with Graph; there is no people
picker yet.

*Phase 7 as shipped:* `/changes` in the side menu; a place's Changes tab
shows the feed with its checks folded below; an identity's "What they can
do" tab ends with the changes to their access; *Data & schedules* lives at
`/admin/data` (Jobs page reworked; `/jobs` redirects; All checks and the
Activity Log are linked from it, no longer in the menu); the freshness line
sits in the side-menu footer. The group-member card waits for a count of
unreadable groups in the API.

*Phase 6 as shipped:* routes `/places/*`, `/people/:id`, `/grants/:id` (the
grant fingerprint), `/findings` ("Needs a look") and `/roles` ("Role guide");
the admin pages keep their routes under the names Daily checks, Activity Log
and Schedules until *Data & schedules* replaces them in phase 7. Home's
"Needs a look" leaves out `unused`; the Place header shows "Access as of"
instead of the snapshot number.

Not in the mockup, to design during the phase that needs it: Role guide
(today's Roles page reworded), the People & apps list, the All grants
expert table, and Settings sub-pages other than *Who is responsible*.

## 5. What already exists

Most of the logic is in place. The work is new tables for what Azure
doesn't give us directly (responsible people, review rounds, a change
feed) and a new UI on top.

| Need | Already there |
|---|---|
| Who can do what on a place | `core/capabilities.py` (levels), `api/scope_capabilities.py` |
| Inherited vs given here, groups expanded | `core/current_view.py`, `group_memberships`, `scan_group_resolutions` |
| Conditions and PIM | `core/conditions.py`, `role_eligibilities`, `assignment_type` / `end_at` |
| Diff between two checks | `diff_scans` in `api/iam_access.py` (`GET /scans/{old}/diff/{new}`; move the comparison into `core/` for the build step in B) |
| What an identity did | `activity_events` with `caller_oid`, `GET /principals/{id}/activity` |
| Worklist and decisions | `findings`, `review_decisions` (by fingerprint) |
| Schedules and job pool | `job_schedules`, `core/scheduler.py` |
| Tree, names | `scopes`, `principals` registries |
| Who did what in RBAC Lens | `audit_events` |
| Since when, by whom | `role_assignments.raw` → `createdOn`, `createdBy`, `updatedOn` (present on every prod row, checked 2026-11-08) |

## 6. New backend pieces

### A. Since when, and given by whom

- [x] Migration `0025`: `role_assignments.created_on`, `created_by`
  (object id, lowercased) and `updated_on`, filled at ingest from the SDK
  object and backfilled from `raw` in the migration.
- [x] Resolve `created_by` through `principals` / Graph. It is often a
  service principal (`sp-terraform`), so say "(pipeline)" when the type
  is `ServicePrincipal`.
- [x] Expose `since` and `given_by` on every grant-shaped response:
  capabilities, assignments, principal access.

### B. Changes feed

One feed answers "what changed, who did it, when". It merges three sources:

| Source | Gives | Exact time? | Who did it? |
|---|---|---|---|
| Activity Log `Microsoft.Authorization/roleAssignments/write\|delete` | direct grants given or removed on subscriptions and below | yes | yes (`caller_oid`) |
| Daily check diff (`diff_scans`) | everything else: grants at management-group level, PIM eligibility, group membership | first seen | no |
| `createdOn` / `createdBy` | grants older than the Activity Log window | yes | yes |

- [x] Migration `0026`: `access_changes` table with the columns
  `id`, `occurred_at`, `first_seen_scan_id`, `kind`, `scope`,
  `principal_id`, `role_definition_id`, `level`, `actor_id`, `source`,
  `activity_event_id`, and `grant_fingerprint`.
  - `kind` is one of `granted | removed | eligible_added | eligible_removed | member_added | member_removed`.
  - `source` is one of `activity_log | daily_check | created_on`.
  - `grant_fingerprint` is `sha256(scope|role GUID|principal)`, the finding fingerprint without the category.
  - Unique on `(grant_fingerprint, kind, occurred_at)`. Index on `(scope, occurred_at)` and `(principal_id, occurred_at)`.
- [x] Build step at the end of each daily check (next to
  `compute_scan_findings`). It diffs against the previous completed check
  of the same root and matches each added or removed grant to a
  roleAssignments event by assignment id. A match gives the exact time and
  actor; without a match the change is a "first seen" entry. Also write
  entries when an activity pull brings in roleAssignments events for grants
  the last check already saw.
- [x] Group membership: diff `group_memberships` between checks and write
  `member_*` entries. The level is the strongest level the group holds.
  Say plainly that the Entra audit log holds who added the member and
  RBAC Lens does not read it.
- [x] `GET /changes?scope=&principal=&since=&kind=&level=&limit=`, plus
  per-week counts for the chart. A place-scoped call includes
  everything inside the place.
- [x] *(365 days for every entry: the feed is small, and a removal decided in a
  review should stay visible for a year.)* Retention: same as events (120 days), except `created_on` entries,
  which describe current grants.

### C. Daily check by default

- [x] On first start, and once by migration for existing installs, create
  two kinds of schedule if missing:
  - a snapshot of the tenant root group every 24 h at 06:00;
  - an Activity Log pull every hour for each subscription in the registry.
  New subscriptions found by the daily check get their pull schedule automatically.
- [x] `job_schedules.at_hour` (nullable): when set, `next_run_at` is
  aligned to that hour (container time zone). Without it, today's
  interval behaviour stays.
- [x] Freshness endpoint for the side-menu footer:
  `GET /freshness → {access_as_of, activity_up_to, failed_last: bool}`.
- [x] *Data & schedules* page: the three source cards (role grants,
  Activity Log, group members with the Graph permission warning), recent
  checks, "Check now", "Check one place now", and history kept. It reuses
  `GET /jobs`, `/schedules`, `core/retention.py`.

### D. Responsible people

- [x] Migration `0027`: `responsibilities` table with
  `scope_id PK FK scopes`, `person_id` (Entra object id), `person_name`,
  `person_email`, `source` (`manual | tag`), `set_by`, and `set_at`.
- [x] `responsible_for(scope)` walks `scopes.parent_id` upward and returns
  the person and the place the setting comes from (shown as "from mg-prod").
  The tree endpoint returns both for every node in one query (recursive CTE).
- [x] Tag suggestions:
  - During discovery, read the tag on subscriptions and resource groups into a new `scopes.tags` JSONB column. The tag name is configurable and defaults to `owner`.
  - A value that looks like an email and matches one Entra user (Graph `users?$filter=mail eq … or userPrincipalName eq …`) becomes a suggestion.
  - Anything else is listed as "could not match" with the raw value, for example `owner = AB`.
  - Ignored suggestions are remembered so they don't come back.
- [x] Endpoints:
  - `GET /responsible/tree`;
  - `PUT /responsible/{scope}` and `DELETE /responsible/{scope}`, admins only and audited;
  - `GET /responsible/suggestions`;
  - `POST /responsible/suggestions/{scope}/use|ignore`.
- [x] Setting someone checks the object id with Graph, to validate input at the boundary.

### E. Monthly reviews

A review is per **grant**, not per finding. Findings stay as evidence on
the grant and in *Needs a look*.

- [x] Migration `0028`:
  - `review_rounds` table: `id`, `scope_id`, `period` (`YYYY-MM`), `reviewer_id`, `opened_at`, `due_at`, `finished_at`. Unique on `(scope_id, period)`.
  - `review_items` table: `round_id`, `grant_fingerprint`, `role_assignment_id` at open time, `decision` (`keep | remove | ask`, null = open), `note`, `decided_by`, `decided_at`, `carried_from_round_id`, and `ask_remind_at`.
- [x] *(Runs after every check and at startup, not at 00:30: the nightly check
  is the natural moment.)* Opening, daily at 00:30 and idempotent:
  - On the 1st, open one round for each place where someone is set as responsible.
  - The round covers grants given on the place and on everything inside it, except parts where someone else is set lower down. Those parts get their own round.
  - The round is due on the last day of the month.
  - Grants inherited from above show read-only with "reviewed at mg-prod" (see open decision 2).
- [x] Carry-over: a grant kept last month and unchanged since (same
  fingerprint, no `access_changes` entry) starts as *kept*, so the
  reviewer only looks at what is new or changed. The reviewer can still
  change it.
- [x] No self-review:
  - If the reviewer is the grant's identity, or a member of its group, the item moves to the round of the next responsible person above.
  - If there is nobody above, it goes to the admins' round.
  - The UI says so on the card ("her own place").
- [x] Places with nobody responsible go into one monthly admins' round.
- [x] *Remove*: the item shows `az role assignment delete --ids <id>`.
  The round has "Copy commands" and "Export list" (CSV). When a later
  daily check no longer sees the grant, the item shows "removed in Azure".
- [x] *Ask*: sets `decision = ask` and `ask_remind_at = now + 7 d`, and
  writes a notification to the outbox (F). The item counts as not decided.
- [x] *Finish review* is enabled only when no item is open or *ask*.
  It stamps `finished_at` and writes an audit event.
- [x] Endpoints:
  - `GET /reviews` (mine / all);
  - `GET /reviews/{id}`;
  - `PUT /reviews/{id}/items/{fingerprint}`;
  - `POST /reviews/{id}/finish`;
  - `GET /reviews/{id}/export`.
  
  A reviewer may only decide on their own rounds. Admins may decide on any round.
- [x] Existing `review_decisions` stay for findings. Keeping a grant
  marks its open findings `reviewed`. A finding the reviewer explicitly
  accepts stays `accepted_risk`.

### F. Notifications, prepared but not connected

- [x] Migration `0029`: `notifications` outbox with `id`, `kind`
  (`ask | review_opened | review_due | reminder`), `recipient_id`,
  `subject_ref`, `payload` JSONB, `created_at`, `sent_at`, `channel`, `error`.
- [x] `core/notify.py`: a `Notifier` protocol (`send(notification) -> None`),
  and a `NullNotifier` that leaves rows unsent. A sender loop in the
  scheduler is disabled while no channel is configured.
- [x] Settings card: "Notifications — not connected yet". Connecting
  email (Graph `sendMail`, needs `Mail.Send`) or Teams (a Workflows
  webhook) is a later phase that only adds a `Notifier`.

### G. Operations in plain words

- [x] *(Shipped in `0025`, refreshed after each pull rather than on a weekly schedule.)*
  Migration `0030`: `operation_names` table (`operation PK`,
  `display_name`, `resource_type_display`), filled weekly from ARM
  `providers/Microsoft.Authorization/providerOperations?$expand=resourceTypes`
  for the providers seen in `activity_events`. Fallback: verb from the
  suffix (`write` → "changed", `delete` → "deleted", `action` → the
  action name) plus the resource type.
- [x] *(Identity "What they did", scope Activity, Activity Log, Grant page. "Last change made
  here" waits for the Changes feed.)* Use it in the identity's "What they did in the last 90 days", in
  "last change made here", and in the Changes feed.

### H. "You": signed-in identity

- [x] Home, "Places I'm responsible for", *my* reviews and the
  side-menu footer need the signed-in user's object id from the Entra
  token (`core/security.py`).
- [x] With sign-in off, the app runs as an admin view.
  Home shows the whole tenant, and "Places I'm responsible for" is
  hidden. Reviews still work, with decisions recorded as `anonymous`.
- [x] Non-admin signed-in users see the places they are responsible for,
  and their own access, and nothing else. This needs a scope filter on
  every read endpoint, so it is phase 9 work, not a UI toggle.

## 7. Phases

Each phase is one migration chain and one release. The previous UI
stays reachable through redirects until the next phase lands.

| Phase | Contents | Depends on | Done when |
|---|---|---|---|
| 6 ✅ | A (since / by), G (operation names), §3 words, new side menu, Home, Places, Place · Who can do what, Identity, Grant (read-only) | — | Every grant on a place page says since when and by whom; no page outside *Data & schedules* says snapshot, scan or job. |
| 7 ✅ | B (Changes feed), C (daily check by default, freshness, *Data & schedules*), Place · Changes, Changes | 6 | A grant given in Azure shows in Changes with exact time and actor within one hour (Activity Log) or by the next morning (daily check). |
| 8 ✅ | D (responsible people, tag suggestions), H part 1 (signed-in identity, "yours") | 6 | Every subscription has a responsible person or shows "nobody — goes to admins"; prod suggestions from `owner` tags are listed. |
| 9 ✅ | E (monthly reviews, Grant decisions), H part 2 (scope-filtered reads for non-admins) | 7, 8 | The November round for a test place can be opened, decided, exported and finished, with no self-review. |
| 10 ✅ | F (notification outbox and interface; Ask and review reminders write to it) | 9 | Ask creates an outbox row; nothing is sent; Settings shows "not connected". |

### Tests per phase

- Backend: pytest for each new core module, using SQLite where today's
  tests do and Postgres-only paths behind the existing marker. Cover:
  - carry-over and self-review routing;
  - inheritance of responsible people;
  - matching changes to Activity Log events (written as fixtures from real prod shapes, with no real ids in the repo).
- Frontend: vitest for the pure helpers (wording, level labels, review
  progress), plus `tsc` through `make check`.
- Each phase is checked on the test stack against a copy of the prod DB
  (`make backup prod`, then restore into test) before it is released.

## 8. Limits to state in the UI

- Azure never records reads or data-plane access. "No change in N days"
  never means "not used".
- Activity Log at management-group level: collected since 2026-11-10 (one
  hourly collection for all management groups). Before that, grants made
  there appeared at the next daily check, as "first seen", with no actor.
- Who added someone to a group is in the Entra audit log
  (`AuditLog.Read.All`), which is not read. A possible later source.
- Some groups can't be read without `GroupMember.Read.All`.
  The *Data & schedules* page says so with the fix.

## 9. Open decisions

1. ~~**Time zone for the daily check.**~~ **Decided 2026-11-08: container
   local time** (`TZ`; UTC when unset).
2. ~~**Inherited grants in a place's review.**~~ **Decided 2026-11-08:**
   reviewed only where they were given, shown read-only below. The mockup's
   prod review item for `grp-prod-owners` (given on mg-prod) moves to the
   mg-prod review.
3. ~~**Groups in reviews.**~~ **Decided 2026-11-08:** the group's grant is one
   item, with its members listed on the card; membership stays managed in
   Entra ID, not decided in RBAC Lens.
4. ~~**Tag name.**~~ **Decided 2026-11-08: a setting from the start**, `owner` by default.
5. ~~**Who may set responsible people.**~~ **Decided 2026-11-08: admins only.**
