"""Advice: what to do about each grant, from a fixed playbook.

Each rule looks at the facts RBAC Lens already holds about one grant: what
the role allows, where it was given, standing or PIM, the identity's kind,
the open findings, who gave it and when. It fires or not. The highest
weight wins, then the playbook order (core/advice_texts.py), so the same
facts always give the same advice. RBAC Lens never changes Azure: the
advice carries the commands, a person runs them.

Findings an admin accepted as a risk, or marked resolved, no longer drive
advice. "No change in 90 days" only counts for R03, where it turns
"make it PIM-eligible" into "remove": Azure never records reads.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.advice_texts import (
    ORDER,
    PIM_PORTAL,
    RULES,
    WEIGHT_RANK,
    create_command,
    delete_command,
)
from app.core.current_view import CurrentView, current_view, grant_id
from app.core.findings import BROAD_LEVELS
from app.core.responsible import Resolved, resolve_all
from app.core.timeutil import as_utc
from app.db.models import AppSetting, RoleAssignment
from app.schemas.advice import (
    Advice,
    AdviceGrantRef,
    AdviceNote,
    AdviceSettings,
    IdentityAction,
    IdentityAdvice,
)
from app.tools.graph_resolver import is_microsoft_app

SETTINGS_KEY = "advice"
_KIND = {
    "User": "person",
    "Group": "group",
    "ForeignGroup": "group",
    "ServicePrincipal": "app",
    "MSI": "app",
    "Application": "app",
}
_LEVEL_WORDS = {
    "mgmt_group": "management group",
    "subscription": "subscription",
    "resource_group": "resource group",
    "resource": "resource",
}
_SCOPE_RANK = {"mgmt_group": 0, "subscription": 1, "resource_group": 2, "resource": 3}
_DONE = ("accepted_risk", "resolved")  # findings that no longer drive advice
NO_ACTION = ("R00 fine", "R15 limited", "R16 microsoft-app")
REDUNDANT = {"R07 redundant", "R08 direct-user"}
PIM_RULES = {"R02 standing-admin", "R05 keys-broad"}


# ---- settings ----------------------------------------------------------------------------


def load_settings(session: Session) -> AdviceSettings:
    row = session.get(AppSetting, SETTINGS_KEY)
    try:
        return AdviceSettings(**(row.value or {})) if row else AdviceSettings()
    except ValidationError:
        return AdviceSettings()


def save_settings(session: Session, settings: AdviceSettings) -> AdviceSettings:
    """Store validated settings (ids lowercased, deduplicated). The caller commits."""
    clean = settings.model_copy(
        update={
            "break_glass": sorted({p.strip().lower() for p in settings.break_glass if p.strip()})
        }
    )
    row = session.get(AppSetting, SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=SETTINGS_KEY, value=clean.model_dump()))
    else:
        row.value = clean.model_dump()
    return clean


# ---- context -----------------------------------------------------------------------------


@dataclass
class AdviceContext:
    view: CurrentView
    settings: AdviceSettings
    responsible: dict[str, Resolved]
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    _groups: dict[str, set[str]] = field(default_factory=dict)
    _group_roles: dict[tuple[str, str], list[str]] | None = None

    def groups_of(self, pid: str) -> set[str]:
        if pid not in self._groups:
            self._groups.update(self.view.groups_of({pid}))
            self._groups.setdefault(pid, set())
        return self._groups[pid]

    def prime_groups(self, pids: set[str]) -> None:
        """Load group memberships for many identities in one go (the report)."""
        missing = pids - self._groups.keys()
        if missing:
            found = self.view.groups_of(missing)
            for pid in missing:
                self._groups[pid] = found.get(pid, set())

    @property
    def group_roles(self) -> dict[tuple[str, str], list[str]]:
        """``{(scope, role guid): [group ids]}`` for grants held by groups."""
        if self._group_roles is None:
            idx: dict[tuple[str, str], list[str]] = defaultdict(list)
            for ra in self.view.ras:
                if _KIND.get(ra.principal_type) == "group":
                    idx[(ra.scope.lower(), _guid(ra))].append(ra.principal_id.lower())
            self._group_roles = idx
        return self._group_roles


def context(session: Session, view: CurrentView | None = None) -> AdviceContext:
    resolved, _ = resolve_all(session)
    return AdviceContext(
        view=view or current_view(session), settings=load_settings(session), responsible=resolved
    )


def _guid(ra: RoleAssignment) -> str:
    return ra.role_definition_id.rsplit("/", 1)[-1].lower()


# ---- facts and rules ---------------------------------------------------------------------


@dataclass
class _Facts:
    ra: RoleAssignment
    kind: str
    level: str
    broad: bool
    standing: bool
    place: str
    role: str
    findings: dict[str, Any]  # category -> Finding, for findings still in play
    guest: bool
    break_glass: bool
    microsoft: bool


def _facts(ctx: AdviceContext, ra: RoleAssignment) -> _Facts:
    view = ctx.view
    scope = ra.scope.lower()
    pid = ra.principal_id.lower()
    return _Facts(
        ra=ra,
        kind=_KIND.get(ra.principal_type, "other"),
        level=view.capability_of(ra).level,
        broad=ra.scope_level in BROAD_LEVELS,
        standing=ra.assignment_type != "Activated" and ra.end_at is None,
        place=view.name.get(scope)
        or ra.scope_name
        or scope.rstrip("/").rsplit("/", 1)[-1]
        or scope,
        role=ra.role_name or _guid(ra),
        findings={
            ref.finding.category: ref.finding
            for ref in view.findings_by_ra.get(ra.id, ())
            if ref.status not in _DONE
        },
        guest="#ext#" in (ra.principal_name or "").lower(),
        break_glass=pid in ctx.settings.break_glass,
        microsoft=is_microsoft_app(getattr(ra, "principal_app_owner", None)),
    )


def _best_role(finding: Any) -> str | None:
    recs = (finding.evidence or {}).get("recommendations") or []
    return recs[0].get("role_name") if recs else None


def _chain(ctx: AdviceContext, ra: RoleAssignment) -> list[str]:
    """The grant's place, the registered place that holds it, and everything above."""
    scope = ra.scope.lower()
    owner = ctx.view.owner.get(ra.id)
    out = [scope]
    if owner and owner != scope:
        out.append(owner)
    out.extend(ctx.view.ancestors(owner or scope))
    return out


def _fired(ctx: AdviceContext, f: _Facts) -> list[tuple[str, str, dict[str, Any]]]:
    """``[(rule id, why, extra)]`` for every rule that fires on the grant."""
    ra, out = f.ra, []
    where = f"on the {_LEVEL_WORDS.get(ra.scope_level, 'place')} {f.place}"
    unused = "unused" in f.findings

    def fire(rule: str, why: str, **extra: Any) -> None:
        out.append((rule, why, extra))

    if f.microsoft:
        # Its roles are set by the Azure service that uses it; the other rules don't apply.
        fire(
            "R16 microsoft-app",
            f"{ra.principal_name or 'This app'} is an identity Microsoft runs for an "
            f"Azure service (Defender for Cloud, AKS, backup…). That service gave it {f.role} {where} and "
            "needs it to work. Keep it while the service is in use; turning the service off removes it.",
        )
        _place_notes(ctx, f, fire)
        return out

    if "unresolved_principal" in f.findings:
        fire(
            "R01 orphan",
            f"Entra ID no longer knows this identity, yet it holds {f.role} {where}. "
            "If its id were ever reused, the access would come back.",
        )
    admin_high = f.level == "access" and f.broad and not f.break_glass
    # Apps mostly read, which Azure never records: "not used" is no reason to remove one (R09 covers apps).
    if admin_high and unused and f.kind != "app":
        days = (f.findings["unused"].evidence or {}).get("window_days") or 90
        fire(
            "R03 idle-admin",
            f"{f.role} {where} lets them change who has access to everything inside, "
            f"and no change was made with it in {days} days (reads are not recorded).",
        )
    elif admin_high and f.standing and f.kind != "app":
        fire(
            "R02 standing-admin",
            f"{f.role} {where} lets them change who has access to everything "
            "inside, all the time. With PIM they switch it on only when needed, with a reason.",
        )
    if f.guest and f.level in ("access", "keys", "change"):
        fire("R04 guest-power", f"A guest from another organisation holds {f.role} {where}.")
    if (
        f.level == "keys" and f.broad and f.standing and f.kind in ("person", "group")
    ):  # apps can't use PIM
        fire(
            "R05 keys-broad",
            f"{f.role} {where} lets them read keys, secrets or passwords everywhere inside.",
        )
    if "over_broad" in f.findings:
        best = _best_role(f.findings["over_broad"])
        fire(
            "R06 over-broad",
            f"Only part of {f.role} was used for changes; "
            f"{best or 'a smaller role'} allows every change that was made.",
            best=best,
        )
    if "redundant" in f.findings:
        above = (f.findings["redundant"].evidence or {}).get("covered_by") or []
        names = ", ".join(
            f"{c.get('role_name') or 'a role'} on {c.get('scope_name') or c.get('scope')}"
            for c in above
        )
        fire(
            "R07 redundant",
            f"The same identity already holds {names or 'an equal or stronger role above'}, "
            "which applies here too.",
        )
    if f.kind == "person":
        groups = ctx.groups_of(ra.principal_id.lower())
        if groups:
            guid = _guid(ra)
            via = [
                g
                for s in _chain(ctx, ra)
                for g in ctx.group_roles.get((s, guid), ())
                if g in groups
            ]
            if via:
                fire(
                    "R08 direct-user",
                    f"They also get {f.role} here through a group they are in, "
                    "so the direct grant adds nothing and is easy to forget.",
                    group=via[0],
                )
    if f.kind == "app" and f.level == "access":
        best = _best_role(f.findings["over_broad"]) if "over_broad" in f.findings else None
        fire(
            "R09 app-owner",
            f"An app holds {f.role} {where}: whoever controls its secrets or code can "
            "give anyone any access.",
            best=best,
        )
    if "custom_replaceable" in f.findings:
        best = _best_role(f.findings["custom_replaceable"])
        fire(
            "R10 custom-replaceable",
            f"{best or 'A built-in role'} allows every change made with the "
            f"custom role {f.role}; built-in roles are kept up to date by Microsoft.",
            best=best,
        )
    if "membership_unknown" in f.findings:
        fire(
            "R11 unknown-members",
            "The group's members could not be read, so who really has this access is unknown.",
        )
    new = ra.created_on is not None and ctx.now - as_utc(ra.created_on) <= timedelta(
        days=ctx.settings.new_days
    )
    if "drift_added" in f.findings or (new and not ra.created_by):
        giver = ra.created_by_name or "someone RBAC Lens could not identify"
        fire(
            "R12 new-unexplained",
            f"Given recently by {giver}. Check there is a reason for it, such as a change request.",
        )
    if ra.end_at is not None and as_utc(ra.end_at) - ctx.now <= timedelta(
        days=ctx.settings.expiring_days
    ):
        fire(
            "R14 expiring",
            f"It ends on {as_utc(ra.end_at):%d %b %Y}. Extend it only if it is still needed.",
        )
    _place_notes(ctx, f, fire)
    return out


def _place_notes(ctx: AdviceContext, f: _Facts, fire: Any) -> None:
    """Facts about the place and the condition, true whatever else applies."""
    owner = ctx.view.owner.get(f.ra.id)
    if owner and owner not in ctx.responsible:
        fire(
            "R13 no-responsible",
            f"Nobody is responsible for {ctx.view.name.get(owner) or owner}, "
            "so nobody reviews this grant except the admins.",
        )
    if f.ra.condition:
        fire(
            "R15 limited", "A condition limits what it allows; keep it if the condition still fits."
        )


# ---- how ---------------------------------------------------------------------------------


def _how(f: _Facts, rule: str, extra: dict[str, Any]) -> tuple[list[str], list[str]]:
    ra = f.ra
    action = RULES[rule].action
    delete = delete_command(ra.principal_id, f.role, ra.scope)
    portal = f"Or in the Azure portal: {f.place} › Access control (IAM) › Role assignments."
    if action == "remove":
        return ["Remove the grant with the command below.", portal], [delete]
    if action == "pim":
        steps = [PIM_PORTAL.format(place=f.place, role=f.role), "Then remove the standing grant:"]
        if rule == "R05 keys-broad":
            steps.insert(
                0,
                "Give it only on the key vaults or resources that need it, or make it PIM-eligible:",
            )
        return steps, [delete]
    if action == "replace":
        best = extra.get("best")
        if best:
            create = create_command(ra.principal_id, ra.principal_type, best, ra.scope)
            return [f"Give {best} on {f.place}, check nothing breaks, then remove {f.role}."], [
                create,
                delete,
            ]
        return [
            (
                f"Find the smallest role it needs (its recent changes are on the grant page), give it, "
                f"then remove {f.role}."
            )
        ], [delete]
    if action == "ask":
        who = "the person who invited the guest" if rule == "R04 guest-power" else "whoever gave it"
        return [
            f"Ask {who}, or the person responsible for {f.place}, whether it is still needed.",
            "If not, remove it:",
        ], [delete]
    if action == "fix_members":
        return [
            (
                "Give the RBAC Lens app registration the Microsoft Graph application permission "
                "GroupMember.Read.All, with admin consent; the next daily check reads the group."
            ),
            "Or check the members in Entra ID › Groups.",
        ], []
    if action == "set_responsible":
        return ["An admin sets someone in Settings › Who is responsible."], []
    return [], []


# ---- advice ------------------------------------------------------------------------------


def _sort_key(item: tuple[str, str, dict[str, Any]]) -> tuple[int, int]:
    return WEIGHT_RANK[RULES[item[0]].weight], ORDER[item[0]]


def advise_grant(ctx: AdviceContext, ra: RoleAssignment) -> Advice:
    f = _facts(ctx, ra)
    fired = sorted(_fired(ctx, f), key=_sort_key)
    names = {x[0] for x in fired}
    skip = {"R13 no-responsible"}
    if names & REDUNDANT:
        skip |= PIM_RULES  # PIM on a grant that another grant duplicates changes nothing: remove it
    actionable = [x for x in fired if x[0] not in skip]
    rule, why, extra = (
        actionable[0]
        if actionable
        else ("R00 fine", "No rule of the playbook applies to this grant.", {})
    )
    r = RULES[rule]
    how, commands = _how(f, rule, extra)
    return Advice(
        rule=rule,
        weight=r.weight,
        action=r.action,
        decision=r.decision,
        title=r.title,
        why=why,
        how=how,
        commands=commands,
        controls=list(r.controls),
        also=[
            AdviceNote(rule=x[0], weight=RULES[x[0]].weight, title=RULES[x[0]].title)
            for x in fired
            if x[0] != rule
        ],
    )


def advise_identity(ctx: AdviceContext, principal_id: str, limit: int = 5) -> IdentityAdvice:
    """'What to do' for one identity: its own grants' advice, one line per rule."""
    pid = principal_id.lower()
    ras = [ra for ra in ctx.view.ras if ra.principal_id.lower() == pid]
    ctx.prime_groups({pid})
    by_rule: dict[str, list[tuple[RoleAssignment, Advice]]] = defaultdict(list)
    fine = 0
    for ra in ras:
        adv = advise_grant(ctx, ra)
        if adv.rule in NO_ACTION:
            fine += 1
        else:
            by_rule[adv.rule].append((ra, adv))

    actions: list[IdentityAction] = []
    for rule, items in by_rule.items():
        items.sort(key=lambda x: (_SCOPE_RANK.get(x[0].scope_level, 9), x[0].scope.lower()))
        first = items[0][1]
        why = (
            first.why
            if len(items) == 1
            else f"{first.why} Same for {len(items) - 1} more grant{'s' if len(items) > 2 else ''}."
        )
        actions.append(
            IdentityAction(
                rule=rule,
                weight=first.weight,
                action=first.action,
                decision=first.decision,
                title=first.title,
                why=why,
                how=first.how,
                controls=first.controls,
                grants=[
                    AdviceGrantRef(
                        grant_id=grant_id(ra),
                        role_name=ra.role_name,
                        scope=ra.scope,
                        scope_name=ctx.view.name.get(ra.scope.lower()) or ra.scope_name,
                        scope_level=ra.scope_level,
                        commands=a.commands,
                    )
                    for ra, a in items
                ],
            )
        )
    actions.sort(
        key=lambda a: (
            WEIGHT_RANK[a.weight],
            min(_SCOPE_RANK.get(g.scope_level, 9) for g in a.grants),
            -len(a.grants),
            ORDER[a.rule],
        )
    )
    return IdentityAdvice(actions=actions[:limit], more=max(0, len(actions) - limit), fine=fine)
