# Plan — advice, to-dos and the assessment report

Status: **shipped** (phases 11–14, 2026-11-08/09). This plan follows
[plan-who-can-do-what.md](plan-who-can-do-what.md), whose phases 6–10
shipped. Phases are numbered from 11. Tick them as they ship and add a line
to `docs/CHANGELOG.md`.

RBAC Lens now shows who can do what, where and since when. This plan
adds the next step: what to do about it.

1. **Advice:** advice on every grant and identity.
2. **To-dos:** a review that ends with a to-do list, checked against Azure.
3. **Report:** an assessment report that says what matters, mapped to
   ISO/IEC 27001:2022.

---

## 1. Decisions taken

Decided with the product owner on 2026-11-08.

1. **Advice comes from fixed rules.** A written playbook (§3) holds the
   rules: the same facts always give the same advice, and every line names
   its rule. This keeps it explainable to an auditor.
2. **AI wording is optional.** It is off by default. When an admin turns it
   on, the Claude API rewrites the rule's explanation in plain language for
   one grant or identity. It never changes the action or the decision.
3. **The report is a page in the app.** It has a print layout ("Save as
   PDF" from the browser) and a CSV annex with the details.
4. **The report maps to ISO/IEC 27001:2022 Annex A.** Each section names
   the controls it gives evidence for and gives a status: *met*, *partly
   met* or *not met*.
5. **Reviews pre-select the advised decision.** The reviewer confirms each
   item or uses "Accept all advice". A finished round produces a **to-do
   list**: the changes to make in Azure. The next daily check ticks each
   to-do off as it sees the change.
6. **Unchanged:**
   - RBAC Lens never changes Azure;
   - "no change in N days" is evidence, never a fault on its own, because
     Azure does not record reads;
   - the words of the earlier plan (place, grant, responsible) still apply.

## 2. What the advice is built from

Everything below is already in the database. No new Azure permission is
needed.

| Fact | Source |
|---|---|
| What the role allows (access, keys, change, data, view) | capability levels |
| Where it was given (MG, subscription, RG, resource) and what it covers | grant scope, tree |
| Standing, PIM-eligible, activated, end date | assignments, eligibilities |
| Direct or through a group, group members, unreadable groups | memberships |
| Limited by a condition (ABAC) | conditions |
| Since when, who gave it | `createdOn` / `createdBy` |
| Last change made with it (writes only) | Activity Log |
| Where it was actually used (writes) | Activity Log by scope |
| Open findings | `high_risk_broad_scope`, `unresolved_principal`, `over_broad`, `custom_replaceable`, `redundant`, `membership_unknown`, `drift_added` |
| Identity kind: person, guest (`#EXT#` in the UPN), group, app | principals |
| Who publishes an app (Microsoft or the tenant), managed identity | Graph `appOwnerOrganizationId`, `servicePrincipalType`, on each grant row |
| Responsible person for the place | responsibilities |

## 3. The playbook

Each rule is a function over one grant, or over one identity, and returns
nothing or an **advice** made of:

- `rule`: a stable id;
- `weight`: **must**, **should** or **could**;
- `action`: one of the actions in the table;
- `decision`: the review decision it suggests (keep, remove, change or ask);
- `why`: one sentence built from the facts;
- `how`: the `az` command or portal steps;
- `controls`: the ISO controls it gives evidence for.

| Id | When | Weight | Action → decision | ISO |
|---|---|---|---|---|
| R01 orphan | The identity no longer exists in Entra ID | must | remove → remove | 5.16, 5.18 |
| R02 standing-admin | Can change access, at MG or subscription, standing (not PIM) | must | make PIM-eligible → change | 8.2 |
| R03 idle-admin | A person or group: R02 and no change made with it in 90 days (apps mostly read, which Azure never records) | must | remove, or PIM if still needed → remove | 8.2, 5.18 |
| R04 guest-power | Guest who can change access, get keys, or change resources | must | ask the sponsor → ask | 5.16, 8.2 |
| R05 keys-broad | A person or group can get keys and passwords at MG or subscription, standing (apps can't use PIM) | should | narrow to the resources used, or PIM → change | 5.17, 8.2 |
| R06 over-broad | Only part of the role was used for changes; a smaller built-in role allows them all (`over_broad`) | should | replace with that role → change | 5.15 |
| R07 redundant | The same or a stronger role covers it already, from above or through a group | should | remove the extra grant → remove | 5.15 |
| R08 direct-user | A person holds it directly, while a group gives the same role on the same place | should | remove the direct grant → remove | 5.15, 5.18 |
| R09 app-owner | An app (service principal) can change access | should | replace with the least role it needs → change | 8.2 |
| R10 custom-replaceable | A custom role matches a built-in one (`custom_replaceable`) | could | replace with the built-in role → change | 5.15 |
| R11 unknown-members | Given to a group whose members can't be read | should | grant `GroupMember.Read.All`, or check the members in Entra → ask | 5.18 |
| R12 new-unexplained | Given in the last 30 days, giver unknown or outside the admins | should | confirm with the giver → ask | 5.18, 8.15 |
| R13 no-responsible | The place has nobody responsible (identity: one of its places) | should | set someone → none (an admin task) | 5.18 |
| R14 expiring | Time-bound, ends within 14 days | could | nothing, or extend it on purpose → keep | 5.18 |
| R15 limited | A condition limits it | — | lower risk; keep if the condition still fits → keep | 5.15 |
| R16 microsoft-app | An app Microsoft publishes (`appOwnerOrganizationId` is a Microsoft tenant, read from Graph at each check), such as Defender for Cloud or AKS. Its roles are set by the service, so no other rule applies (R13 and R15 stay as notes) | — | keep → keep | 5.15 |
| R00 fine | No rule fires | — | keep → keep | — |

How rules combine:

- **One action per grant:** the highest weight wins, then the order in the
  table. The other rules that fired are listed under it as "also".
- **Redundancy beats PIM.** When R07 or R08 fires, R02 and R05 can't be the
  action: making a duplicated grant PIM-eligible changes nothing, so it is
  removed. The grant it duplicates gets its own advice.
- **Identity advice** ("What to do" for a person or app) is the grants'
  advice grouped by action and ranked by weight and scope breadth, plus
  identity-level rules (R01, R04, R11). It shows the top 5, then "and N
  more".
- **Thresholds** (90 days idle, 30 days new, 14 days expiring) are settings,
  with these values as defaults.

`how` text, for example:

- **remove:** the existing `az role assignment delete` command.
- **PIM:** the portal path *Entra ID › PIM › Azure resources › {place} ›
  Roles › {role} › Add assignments › Eligible*, plus the removal command for
  the standing grant.
- **move down:** `az role assignment create` on the child place, then the
  delete command.

## 4. Screens

**Grant page.** An "Advice" box sits under the title: the verdict in a
sentence, the action, why, how (with a copy button), "also" (the other
rules), and the ISO controls in small type.

**Person / app page.** A "What to do" box sits above the tabs: up to 5
ranked actions, each naming its grants. In the map view, each grant chip
carries a small marker (must: red dot, should: amber dot).

**Review round.**
- Each item shows the advice and has the suggested decision pre-selected,
  marked *suggested* until the reviewer confirms or changes it.
- "Accept all advice" confirms every suggested decision still open, after a
  summary ("12 keep · 3 remove · 2 change · 1 ask").
- The round can't be finished while a suggestion is unconfirmed.
- New decision: **Change**, meaning keep the access but change how it is
  given (PIM, narrower place, other role). It needs a short note.

**To do** (new item in the side menu under *To do*).
- When a round is finished, every **remove** and **change** becomes a
  to-do. Each to-do holds:
  - the grant;
  - the advice's `how`;
  - who decided it;
  - its status: *open*, *done* (seen by the next check), or *dismissed*,
    with a reason.
- A to-do is **done** when the daily check no longer sees the original
  grant. For change to-dos, the page also shows the new grant that
  replaced it, when there is one.
- Filters: mine, my places, all (admins). Export as CSV and as a script of
  `az` commands for the remove to-dos.
- Overdue: open 14 days after the round finished. Overdue to-dos are
  listed on Home and sent in the reminder email.

**Report** (*Explore › Report*, `/report?scope=…`).
- Pick the whole tenant, a management group or a subscription. Admins see
  everything; a responsible person sees their places.
- Print-ready: Save as PDF from the browser gives clean A4 pages, with the
  date and "as of" on each page.

## 5. The assessment report

The report has these sections, in this order.

1. **Summary:**
   - One paragraph: scope, as of, how many identities and grants.
   - The 5 things that matter most: the top must-advice across the scope,
     counted ("7 people can change who has access on the whole tenant, 5
     of them standing").
   - A control table with one line per control and its status.
2. **Privileged access** (8.2): who can change access or get keys, where,
   standing vs PIM, guests and apps among them, idle ones.
3. **Identities** (5.16): orphaned identities, guests with power, groups
   whose members can't be read.
4. **Least privilege** (5.15, 5.17): over-broad, redundant and
   direct-plus-group grants, replaceable custom roles, keys at broad scope.
5. **Access reviews** (5.18):
   - Places with and without a responsible person.
   - The last rounds: finished on time, items decided.
   - To-dos open and overdue, and how long they took to close.
6. **Changes and evidence** (8.15): grants given and removed in the period,
   unexplained new grants, Activity Log coverage and retention, and how
   fresh the daily check is.
7. **Recommendations:** every advice in the scope, grouped by action and
   ranked, with counts and a link to each list.
8. **Annex** (CSV download): every grant with its level, how it is given,
   since when, by whom, its advice, rule and ISO controls. Shipped as CSV
   only: printing every row made the PDF several times longer without
   adding to the assessment.

Status per control, by default. The thresholds are settings.

| Control | Met | Partly met | Not met |
|---|---|---|---|
| 8.2 Privileged access | no standing "change access" at MG/subscription, except break-glass identities listed in settings | 1–3 such grants | more than 3, or any guest among them |
| 5.16 Identity management | no orphan, no unreadable group | orphans or unreadable groups, all under review | orphans not under review |
| 5.15 Access control | no must or should advice from R06–R10 | fewer than 10 | 10 or more |
| 5.17 Authentication information | no standing keys access at MG/subscription | at subscription only | at MG or tenant |
| 5.18 Access rights | every subscription has a responsible person, the last round finished on time, no overdue to-dos | one of the three missing | two or more missing |
| 8.15 Logging | every subscription collected hourly, daily check under 26 h old | gaps under 7 days | larger gaps, or no daily check |

**Saved assessments.**
- "Save this assessment" stores the report's numbers and statuses, with
  date and author, so later reports can show "since the last assessment:
  Owner standing 7 → 2".
- Saved assessments are read-only, so the report stays reproducible as
  evidence.

## 6. Optional AI wording

- Settings › Advice › AI wording: off by default. Turning it on needs an
  Anthropic API key, stored encrypted like the Azure credentials
  (`ENCRYPTION_KEY`) or taken from `ANTHROPIC_API_KEY`, and a model choice
  (default: Claude Opus 5.5; Sonnet 5.5 and Haiku 5.5 also offered).
  Explanations are made on request ("Explain in plain words"), not on every
  page load.
- **Data sent:**
  - the rule ids and facts of one grant or identity;
  - role names, place names and levels;
  - day counts.

  Names of people, UPNs and object ids are replaced by "the person",
  "group A" and so on. Settings shows exactly what is sent.
- The text is cached by a hash of the facts, so the same facts give the
  same text and no repeat calls. It is marked *AI wording* and never
  replaces the rule text, which stays one click away.
- Never in the report's control status or numbers. Optionally, it can
  write the report's summary paragraph from the computed numbers.

## 7. Backend pieces

- `core/advice.py`: the rules (pure functions over a fact bundle), the
  combiner, and identity grouping. The facts come from `CurrentView` plus
  findings. Target under 400 lines; rule texts go in
  `core/advice_texts.py`.
- **API:**
  - `GET /api/grants/{id}` and `GET /api/principals/{id}` gain `advice`;
  - `GET /api/advice?scope=` returns all advice in a scope, used by the
    report and the round.
- **Reviews:**
  - `review_items` gains `suggested` (decision), `suggested_rule` and
    `confirmed_at`;
  - the decision check allows `change`;
  - `POST /api/reviews/{id}/accept-advice`.
- **To-dos** (migration 0030): `todos` table with id, round_id, item_id,
  grant fingerprint, action, how, status, created_at, done_at, dismissed
  reason and by.
  - Created on finish.
  - Closed by the daily check: a hook after `open_and_sync`.
- **Report:** `core/report.py` builds the sections from the advice and
  existing stats. `GET /api/report?scope=` returns JSON, plus `/annex.csv`.
  Saved assessments go in `assessments` (migration 0031): id, scope,
  created_at, by, JSON numbers.
- **AI:** `core/ai_wording.py`, behind a setting. It uses the Anthropic
  SDK, with a cache table `ai_texts` (hash → text). Off means no import
  and no call.
- **Visibility:** `/api/advice`, `/api/report` and `/api/todos` join the
  guard's allow-list, filtered to the person's places like the other
  lists.

## 8. Phases

| Phase | Contents | Done when |
|---|---|---|
| 11 ✅ | Playbook R00–R15, `core/advice.py`, advice on the Grant page and "What to do" on the person/app page, threshold settings | Every grant page shows one action with its rule; a person page lists their top actions; rules are covered by tests built from real prod shapes (no real ids) |
| 12 ✅ | Reviews: suggested decision, Accept all advice, Change decision; To do list, closed by the daily check; overdue on Home and in reminders | A test round is finished with advice accepted; its remove to-dos close after a check that no longer sees the grants |
| 13 ✅ | Report page, print layout, CSV annex, ISO control table, saved assessments and comparison | A tenant report prints to a clean PDF; saving and re-opening an assessment shows the same numbers |
| 14 ✅ | Optional AI wording (settings, encrypted key, cache, what-is-sent view) | With AI off nothing changes; with it on, a grant shows an AI-worded explanation marked as such, and no name or id leaves the machine |

Each phase is tested as before: pytest per core module, vitest for helpers,
`make check` and `make lint`, then a pass on the test stack against a copy
of the prod data.

## 9. Open decisions

1. **Break-glass identities.** Which identities are exempt from R02 and R03
   and from the 8.2 status. Proposal: a list in Settings that only admins
   edit, shown in the report.
2. **Who sees the report.** Proposal: admins see every scope; a responsible
   person sees the report for their places only.
3. **Change decisions.** Proposal: a change to-do is done when the original
   grant is gone, and the next round reviews the new grant like any other.
4. **The AI model and where it runs.** Proposal: Anthropic API directly. If
   data must stay in Azure, Claude through Microsoft Foundry, with the same
   interface.
