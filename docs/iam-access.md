# IAM Access — feature guide

RBAC Lens answers *"who has what access where"* across an Azure tenant,
and helps review it every month. This page walks through what the UI does.
Routes, fields and rules are in [api.md](api.md); tables in
[data-model.md](data-model.md).

## What the Azure identity needs

The stacks sign in as a read-only service principal (see
[deployment.md](deployment.md#azure-identity-least-privilege)): **Reader**
on the management groups to check, **Management Group Reader** on the
Tenant Root Group, and the Entra role **Directory Readers** for names,
group members and who holds which Entra role in Microsoft Graph. How to set it up:
[deployment.md](deployment.md#setting-up-the-service-identity).

Without Graph read access, identities show as GUIDs and groups get a
*Group members unknown* finding: a group's grant can't be judged
"unused" when its members' activity is unknown. Everything else works.

Optional: the Graph application permission **AuditLog.Read.All** lets
RBAC Lens read the Entra ID audit log, to say who added or removed a group
member ([Entra ID audit log](#entra-id-audit-log)).

## Checks

A check (a "snapshot" in the code) reads every role assignment, PIM
eligibility and group membership under one place. They start from
**Data & schedules** (`/admin/data`; `/jobs` and `/iam-access` redirect
there):

- the **daily check** of the whole tenant runs by itself (06:00 in the
  backend's `TZ`), and each schedule row has **Run now**;
- **Add a schedule, or run once** checks any management group or
  subscription, once or on a schedule;
- a place's page has **Check this place now**;
- **Recent checks and collections**, further down the page, lists past
  runs.

Anything not in the list of places (a single resource, a resource group)
is typed in: pick **Other: type a GUID or path…** and enter one of:

| Input shape                                           | Treated as     |
|-------------------------------------------------------|----------------|
| `mg-platform`                                         | mgmt group     |
| `8a3f1234-1111-2222-3333-aabbccddeeff` (a GUID)       | subscription   |
| `<sub-guid>/rg-prod`                                  | resource group |
| `/providers/Microsoft.Management/managementGroups/x`  | mgmt group     |
| `/subscriptions/<guid>`                               | subscription   |
| `/subscriptions/<guid>/resourceGroups/rg-prod`        | resource group |
| `/subscriptions/<guid>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<name>` | resource |

A check fails when its root can't be read: a management group that
doesn't exist or the identity can't see, a subscription not in its
visible list, or any root whose role assignments can't be listed
(`ScopeUnreachable: …`). Below the root, it depends on what Azure says:

- **Refused (403):** the place is skipped and flagged `unread`. Its own
  grants are unknown, not absent: the last check that read it stays its
  owner, and the changes feed reports nothing as removed there.
- **Gone (404):** skipped; it was deleted while the walk ran.
- **No answer** (network or proxy failure, throttling after retries, a
  server error, a failed sign-in), from Azure or from Microsoft Graph: the
  check fails with `ScanInterrupted: …` or `GraphUnavailable: …` and
  saves nothing, so unread places are never recorded as empty. A
  scheduled check is tried again after 15 minutes.

A check has no results page of its own. `/iam-access/<id>` shows progress
while it runs (or the error) and then opens the place it was rooted at.
The old `…/scan-view`, `…/inspector` and `…/coverage` paths redirect the
same way.

## Places

`/places` is the tenant tree; ⌘K (Ctrl K, or `/`) jumps to any place,
identity or role. `/places/<ARM path>` shows the *current* state of one
place: the newest completed check that walked it, whatever it was rooted
at. Old `/scopes/…` links redirect.

The header has the breadcrumb, the Azure id with **Copy**, who is
responsible, **Export CSV** and **Check this place now**. Tiles show how
old the data is (*Access as of*), the grants that apply (given here, from
above, inside) and what *Needs a look*.

| Tab (`?tab=`) | What it shows |
|---|---|
| **Who can do what** (default, `who`) | Everyone whose access applies here, once, at the strongest thing they can do. See below. |
| **Needs a look** (`findings`) | Findings on grants that apply at or below the place. See [Needs a look](#needs-a-look). |
| **Changes** (`history`) | Every check rooted here or above, the current one marked, with **What changed since #N** and a CSV per check. |
| **Activity** (`activity`) | Activity Log per subscription under the place, the most active grants here, and [activity with no visible grant](#activity-with-no-visible-grant). |
| **All grants** (`access`) | One table of every grant that applies, given here, from above or inside, high-risk first. Filters by text (`?q=` seeds it), source and *High-risk only*. A row opens a drawer with its findings, usage, the identity, the role, and a copyable `az role assignment delete` command. Nothing is changed in Azure from the app. |

### Who can do what

Everyone whose access applies here appears once, at the most powerful
thing they can do:

1. **Can change who has access**: can give anyone, themselves included,
   any of the powers below.
2. **Can get keys and passwords**: storage keys, Key Vault secrets,
   cluster credentials and similar. These unlock everything inside a
   resource and keep working after the role is removed, which is why
   **Contributor** sits here. The row says which keys.
3. **Can change resources**: create, reconfigure or delete, but not fetch
   keys.
4. **Can read or change data**: storage contents, not the resources.
5. **Can only look**.

Roles whose definition was never read show under **Unknown role, check
it**. A grant narrowed by an assignment condition keeps its level and says
how, for example "Can grant any role except Owner, User Access
Administrator and Role Based Access Control Administrator"; conditions the
app doesn't recognise are flagged, never ignored.

**PIM.** Someone *eligible* for a role holds nothing until they activate
it, but can at any time. They appear at that level marked **on request**,
and each level counts them. Grants switched on through PIM or ending on a
date are marked on **All grants** and on the identity page, which also list
eligibility under **Eligible through PIM**. A line under the summary says
what PIM found, or which older checks predate PIM and need refreshing.

Each row says how the access arrives (given here, inherited from a parent,
more roles). Groups show their member count and expand to the people.
**Inside** lists the resource groups and resources below that give someone
more than they have here.

### High-risk roles

A role is high-risk (`app/core/role_risk.py`) when its definition has any
of: the bare `*` action or `*/write`, `*/delete`, `*/action`;
`Microsoft.Authorization/*/write` or `/delete` (it can change access); a
wildcard Key Vault secret data action. This catches custom roles that act
like Owner under an innocuous name, and built-ins like User Access
Administrator. Read-only roles never trigger. The *High-risk role at broad
scope* finding adds the condition that the grant sits on a management
group or subscription.

### Activity with no visible grant

The **Activity** tab lists callers that acted in the current check's
subscriptions without any grant that check saw. Each says whether other
checks hold a grant or group membership for it; **not seen in any check**
(red) is access nobody can explain. Common causes: a grant at a place no
check walked, an Azure platform identity, or a managed identity bound to a
resource.

This needs the Activity Log collected for those subscriptions: the hourly
collection per subscription does it (Data & schedules).

## Entra ID

Some Entra roles lead straight into Azure access: a Global Administrator can
make themselves User Access Administrator over every subscription, an
Application Administrator can add a secret to an app that is Owner of one.
So **Entra ID** is a place of its own, at the top of Places, above the
Tenant Root Group (`/places/providers/microsoft.aadiam`).

The daily check of the Tenant Root Group reads, through Microsoft Graph,
who holds which Entra role: standing, eligible through PIM, or activated
right now, directly or through a group. Its page works like any place's
(Who can do what, Needs a look, Changes, All grants), without an Activity
tab (the Activity Log doesn't record Entra actions) and without **Check this
place now** (the Tenant Root Group's check reads it). Entra roles never flow
down into Azure places: they are a way in, not a grant there.

An Entra role lands at a level by what its permissions allow:

| Level | Examples |
|---|---|
| Can change who has access | Global Administrator, Privileged Role Administrator, Groups / User Administrator (the members of groups that may hold Azure roles), Identity Governance Administrator (access packages) |
| Can get keys and passwords | Application / Cloud Application Administrator (app secrets), Helpdesk / Authentication / Privileged Authentication Administrator (passwords, sign-in methods), Global Reader and Security Reader (BitLocker recovery keys) |
| Can change resources | the other administrator roles; Exchange, SharePoint and Teams administrators say "Microsoft 365 group members" |
| Can only look | Directory Readers, Reports Reader, the reader roles |

A permission limited to objects someone owns doesn't count here. A role
Microsoft doesn't list shows under **Unknown role, check it**. In the role
guide, Entra roles carry an **E**.

**Needs a look** on Entra roles:

- *Entra admin role held all the time*: a person holds a role that can
  change who has access, not through PIM. Break-glass identities (Settings ›
  Advice) are left out.
- *An app holds a powerful Entra role*: whoever has its secret can do the
  same.
- *Too many Global Administrators*: more than four, standing or eligible;
  Microsoft advises fewer than five.
- *Powerful Entra role through a group*: whoever manages its members decides
  who holds it.
- *Powerful role held from another tenant*: a partner's group, through
  delegated admin access (GDAP). Its members are managed in the partner's
  tenant and can't be seen from yours; the row says "another tenant". RBAC
  Lens knows it from the tenant the assignment names, or, for a PIM
  eligibility, because your directory doesn't know the group. The same
  finding applies to a partner's group holding a powerful Azure role.

Usage findings don't apply: no Entra action is in the Activity Log. The
grant drawer's command removes an Entra role through Microsoft Graph
(`az rest --method DELETE …/roleManagement/directory/roleAssignments/<id>`).

With the [Entra ID audit log](#entra-id-audit-log) collected, the Changes
page says who gave or removed an Entra role, directly or through PIM, and an
identity's page lists the Entra roles it **activated through PIM**. Entra
role changes come straight from the audit log too, not only from comparing
two checks: what happened before the first check that read Entra ID (as far
back as Entra keeps it), and a role given and removed between two daily
checks, are on the Changes page. One action recorded twice, by PIM and by
the directory, is one change, credited to the person. An
activation is use of an eligibility, not a change of who has access: it is
never in the changes feed, for Entra or Azure roles.

Entra ID gets a responsible person (Settings › Who is responsible) and a
monthly review like any place; the report's privileged-access control
(ISO/IEC 27001 8.2) counts its standing admin roles.

## People & apps

`/people` lists every user, group and app any check has seen, with current
grants, group count and last Azure activity. `/people/<object id>` answers
*"what can this identity do, and does it?"* (old `/principals/…` links
redirect):

- **What they can do**: every current grant, direct or through a group,
  grouped by subscription, with PIM eligibility; the page carries the
  playbook's advice.
- **Groups** (on a group's page, **Members**): the groups that give it
  access, or the group's transitive members, from the newest check that
  read the group.
- **What they did**: its own Activity Log over 7, 30 or 90 days (a group:
  its members'). Shown to admins and to the person themselves.
- **Needs a look**: findings on those grants.

`/grants/<id>` is one grant in plain words: who, which role, what it
allows, where, since when and from whom, its findings, the advice, and this
month's review decision. With AI wording on, grant and identity pages offer
**Explain in plain words** ([deployment.md](deployment.md#ai-wording-optional)).

## Needs a look

Findings are the reasons a grant needs a look. They are computed at the
end of every check (and again when the Activity Log brings new events) by
matching each identity's operations over 90 days against what its role
allows. For a group, its members' activity counts. Categories and rules
are in [api.md](api.md#findings).

`/findings` is the tenant-wide list over the current grants, worst first,
with search and filters in the URL (`/findings?category=unused`). The same
cards appear on a place's and an identity's **Needs a look** tab. Each card
says what to do, with **Mark reviewed**, **Accept risk**, **Snooze** and
reopen; its **Evidence** shows used versus granted permissions, what was
never used, the top operations and narrower built-in roles. Decisions are
keyed on a fingerprint that is stable across checks, so a reviewed finding
stays reviewed.

## Reviews, To do, Changes and Report

- **Reviews** (`/reviews`): every month, each responsible person (Settings ›
  Who is responsible) reviews the grants given on their places: keep,
  remove, change, or ask the person. The playbook suggests a decision on each
  grant; the rest can be accepted at once. Places without
  anyone go to the admins' round.
- **To do** (`/todos`): each remove or change decision of a finished review,
  with the commands to run. RBAC Lens never changes Azure; the next daily
  check closes the to-do when the grant is gone.
- **Changes** (`/changes`): who got or lost access, when and who did it.
  Each daily check records what changed since the previous one, and the
  history is kept 365 days (`CHANGE_RETENTION_DAYS`): nothing is missed
  between two visits. The first check is the starting point. What it
  covers:

  | | Azure roles | Entra roles | Group members |
  |---|---|---|---|
  | Changes after the first check | yes | yes | yes, for groups that hold access |
  | Changes from before the first check | no: the starting point | yes, as far back as the Entra ID audit log goes (30 days on P1/P2) | no |
  | A grant given and removed between two checks | no; the Activity Log page has both events | yes, from the audit log | no |
  | Who did it | the Activity Log, else Azure's own record | the Entra ID audit log | the Entra ID audit log |

  Without the event, a change is "first seen" by the check, with no
  author. The audit log's group entries only name the author of what a
  check found; they don't make changes of their own.
- **Report** (`/report`): an assessment of the tenant or one place against
  ISO/IEC 27001:2022 Annex A, printable, with a CSV annex. Admins can save
  it as a baseline for later reports.

## Activity Log

`/events` browses the Azure **Activity Log** that was collected:
control-plane operations (writes, deletes, access changes, `listKeys` and
other actions) with filters on subscription, caller, operation, status and
time. A row opens the event's details. Collection runs every hour per
subscription, plus the logs of all management groups, from Data &
schedules; re-reading an overlapping window is safe (events dedupe on
Azure's `event_data_id`).

Routine machine activity older than a few days is kept as one row per day
(caller, operation, resource and outcome, with a count); people's actions,
access and governance changes, and failures are kept in full. Data-plane
activity (who read which blob) is not in the Activity Log and not covered.

## Entra ID audit log

Adding someone to a group, or giving them an Entra role, is a change in
Entra ID, not in Azure, so the Activity Log never records it. When the
identity has **AuditLog.Read.All**, RBAC Lens reads the Entra ID audit log
through Microsoft Graph every hour and keeps its group membership and Entra
role entries (given, removed, eligible through PIM, activated), nothing
else. An Entra role given through PIM is recorded twice, by PIM with the
person who did it and by the directory with the PIM service: the person
wins. On the Changes page, a member who joined or left a
group that holds access then shows who did it and the exact time, *from the
Entra ID audit log*:

- the entry must be for the same member, on that group or a group nested
  inside it (then named: "in *grp-nested*, inside *grp-readers*"), between
  the two checks that saw the difference;
- a change Entra records as made by an app shows "by the app …". That is
  how Entra records a change made through a service, and the person behind
  it, if any, isn't in the entry: *Microsoft Substrate Management* for a
  Microsoft 365 group changed from Outlook or Teams, *Microsoft Approval
  Management* for access packages, *MS-PIM* for PIM for Groups, the Entra
  Connect sync for a group synced from on-premises Active Directory (the
  person is then in the on-premises logs);
- a whole group nested into another brings its members with one entry that
  names the nested group, not them: those members stay "first seen".

Data & schedules says, under **Activity Log coverage**, whether the log can
be read, and why not (the permission, or a licence refusal from Graph). RBAC
Lens tries it after each check, at most once a day while it is refused, and
**Check now** tries it at once. Its hourly collection is added the first
time it can be read. Entra keeps the log 7 days (Entra ID Free) or 30 days
(P1/P2), so changes from before that stay "first seen".

## Role guide

`/roles` lists every role definition read by a check, with **C** (custom)
and **R** (high-risk) marks and how many grants use it. Filters: search,
built-in or custom, *High-risk only*, *Unused only* (cleanup candidates);
sort by name, usage or risk. A role shows its description, `actions` /
`notActions` / `dataActions` / `notDataActions`, assignable scopes, why it
is high-risk, a link to Microsoft's reference for built-ins, and where it is
used (deduplicated over the latest check of each root). Clicking a role
name anywhere opens the same detail in a drawer.

Definitions are cached in `role_definitions` while checking, with no extra
Azure call, and survive a check's deletion. A role no check has read yet
can't be shown; re-run a check that uses it.

## What changed between two checks

`/iam-access/diff/<old>/<new>` compares two completed checks of the same
root; open it with **What changed since #N** on a place's **Changes** tab.
A grant is identified by `(scope, role_definition_id, principal_id)`, so an
Azure re-creation counts as kept, not removed and added. The page lists
what was added and removed, with the count kept.

## Settings

`/settings` has these tabs (admins only):

| Tab | What it does |
|---|---|
| **Who is responsible** | Set the responsible person per place, and use or ignore suggestions from an Azure tag. |
| **Advice** | The playbook's thresholds, break-glass identities, and AI wording. |
| **Notifications** | Channel status, a test message, and the outbox. |
| **Credentials** | Save the Azure identity when the env file has none; the secret is encrypted with `ENCRYPTION_KEY`. Shows which source is active. |
| **Authentication** | Entra ID sign-in ([auth.md](auth.md)). |
| **Audit log** | Every change made through the API. |
| **Database** | Table and index sizes, VACUUM / ANALYZE / REINDEX, retention, and *Delete old checks*. |
| **Changelog** | What changed in each release. |

## What deleting keeps

A **check** is a photo of who has which access on one day. Deleting one
(a run in **Recent checks and collections** on Data & schedules, or
*Delete old checks* on Settings › Database) deletes that photo: the
grants, PIM eligibilities and group members it found, and its findings.
With it go its CSV export, the comparisons with it ("What changed since
#N"), and its line in a place's Changes tab. The check a place shows now
is never deleted (only the API's `force` option can).

Everything else is stored on its own and stays:

| What | Kept for |
|---|---|
| Activity Log events (who did what in Azure) | 120 days (`EVENT_RETENTION_DAYS`) |
| Entra ID audit log entries (group members, Entra roles, PIM activations) | 120 days, like the Activity Log |
| The Changes page (who got or lost access) | 365 days (`CHANGE_RETENTION_DAYS`) |
| Review decisions, to-dos, saved reports, the audit log | not deleted with checks |

RBAC Lens deletes old checks by itself: it keeps the newest 6 completed
checks per place (`SNAPSHOT_KEEP_PER_SCOPE`) and failed ones 7 days. With
one daily check of the tenant root, checks older than about 6 days are
already gone.

A **collection** run (of the Activity Log or the Entra ID audit log) is
only a record: which window it read, when, and whether it worked.
Deleting it, or letting retention delete it after 30 days
(`PULL_RECORD_RETENTION_DAYS`), keeps every event it collected. How far
each log is collected is kept on its schedule, so the next run carries on
from there.

Deleting a **schedule** stops what it runs and keeps everything it
collected. A schedule RBAC Lens added by itself (the daily check, an hourly
collection) is not added again once deleted; pausing it is the way to stop
it for a while.

## What's not covered

- **Resources** are walked only when the check's target is a resource.
  Subscription and resource-group checks don't list each resource; grants
  on resources still appear when Azure returns them with the resource
  group's.
- **Deny assignments** (`Microsoft.Authorization/denyAssignments`) are not
  read.
- **Data-plane activity** (blob reads, secret reads) needs diagnostic
  settings and Log Analytics, which RBAC Lens doesn't read.
