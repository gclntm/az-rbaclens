"""Findings on the owners of apps and groups that hold powerful access (plan "owners").

An app's owner can add a secret to it and act as it; a group's owner decides
its members. Only grants that can change who has access or get keys and
passwords are flagged (decision 2), on the grants of the current check:

- ``app_owners``: people own the app (high on Entra ID, a management group
  or a subscription, else medium);
- ``app_owned_by_app``: another app owns it, so whoever controls that app
  controls this one (high);
- ``group_owners``: someone owns the group and so decides who has its access
  (medium; high on Entra ID, a management group or a subscription);
- ``guest_owner``: a guest (``#EXT#``) owns the app or group (high).

Owners who already hold the same power or more at that place (directly,
through a group, or eligible through PIM) are left out: owning gives them
nothing new. So are break-glass identities (Settings › Advice). Managed
identities have no owners; Microsoft's apps and apps registered in another
tenant get none of these. Owners a check couldn't read raise nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.capabilities import LEVEL_RANK, classify
from app.core.owners import OwnerRef, current_owners
from app.db.models import RoleAssignment, RoleDefinition, Scan
from app.tools.graph_resolver import is_microsoft_app

HIGH, MEDIUM = "high", "medium"
CATEGORIES = ("guest_owner", "app_owned_by_app", "app_owners", "group_owners")
POWERFUL = ("access", "keys")
_BROAD = {"directory", "mgmt_group", "subscription"}
_WHAT = {
    "access": "change who has access",
    "keys": "get keys and passwords",
}


def _is_guest(o: OwnerRef) -> bool:
    return "#ext#" in (o.owner_name or "").lower()


def _owner_evidence(owners: list[OwnerRef], vias: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {"id": o.owner_id, "name": o.owner_name, "type": o.owner_type, "via": vias[o.owner_id]}
        for o in owners
    ]


def _count(n: int, one: str, many: str) -> str:
    return f"1 {one}" if n == 1 else f"{n} {many}"


class _AlreadyHolds:
    """Does an owner already hold a level at a place, or above it, without owning?

    Its own current grants and eligibilities, and those of the groups it is
    in, at the place or a place above it (``core/current_view.py``).
    """

    def __init__(self, session: Session, owner_ids: set[str]):
        from app.core.current_view import current_view

        self.view = current_view(session)
        self.groups = self.view.groups_of(owner_ids) if owner_ids else {}
        self.rows: dict[str, list[Any]] = {}
        for row in [*self.view.ras, *self.view.eligibilities]:
            self.rows.setdefault(row.principal_id.lower(), []).append(row)

    def __call__(self, owner_id: str, scope: str, level: str) -> bool:
        chain = {scope.lower(), *self.view.ancestors(scope.lower())}
        holders = {owner_id, *self.groups.get(owner_id, set())}
        return any(
            row.scope.lower() in chain
            and LEVEL_RANK[self.view.capability_of(row).level] <= LEVEL_RANK[level]
            for h in holders
            for row in self.rows.get(h, ())
        )


def owner_findings(session: Session, scan: Scan, break_glass: set[str]) -> list:
    """Finding drafts for the powerful grants of ``scan`` held by owned apps and groups."""
    from app.core.findings import FindingDraft

    ras = [
        ra
        for ra in session.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.scan_id == scan.id)
            .where(RoleAssignment.principal_type.in_(("ServicePrincipal", "Group")))
        ).all()
        if not (
            ra.principal_type == "ServicePrincipal"
            and is_microsoft_app(getattr(ra, "principal_app_owner", None))
        )
    ]
    if not ras:
        return []
    guids = {ra.role_definition_id.rsplit("/", 1)[-1].lower() for ra in ras}
    levels = {
        rid.rsplit("/", 1)[-1].lower(): classify(perms).level
        for rid, perms in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions)
        ).all()
        if rid.rsplit("/", 1)[-1].lower() in guids
    }
    ras = [
        ra for ra in ras if levels.get(ra.role_definition_id.rsplit("/", 1)[-1].lower()) in POWERFUL
    ]
    if not ras:
        return []
    owners = current_owners(session, {ra.principal_id for ra in ras})
    bg = {b.lower() for b in break_glass}
    already = _AlreadyHolds(session, {o.owner_id for info in owners.values() for o in info.owners})

    drafts: list[FindingDraft] = []
    for ra in ras:
        info = owners.get(ra.principal_id.lower())
        if info is None or not info.known or info.registered_elsewhere:
            continue
        level = levels[ra.role_definition_id.rsplit("/", 1)[-1].lower()]
        people = [
            o
            for o in info.people
            if o.owner_id not in bg and not already(o.owner_id, ra.scope, level)
        ]
        if not people:
            continue
        vias: dict[str, list[str]] = {}
        for o in info.owners:
            vias.setdefault(o.owner_id, []).append(o.via)
        role = ra.role_name or ra.role_definition_id.rsplit("/", 1)[-1]
        who = ra.principal_name or ra.principal_id
        place = ra.scope_name or ra.scope.rstrip("/").rsplit("/", 1)[-1] or ra.scope
        broad = ra.scope_level in _BROAD
        is_app = ra.principal_type == "ServicePrincipal"
        what = f"{role} on {place}, which can {_WHAT[level]}"

        def add(
            category: str,
            severity: str,
            detail: str,
            suggestion: str,
            listed: list[OwnerRef],
            ra: RoleAssignment = ra,
            level: str = level,
            who: str = who,
            role: str = role,
            place: str = place,
            vias: dict[str, list[str]] = vias,
            app_id: str | None = info.app_id,
        ) -> None:
            drafts.append(
                FindingDraft(
                    role_assignment_id=ra.id,
                    scope=ra.scope,
                    role_definition_id=ra.role_definition_id,
                    principal_id=ra.principal_id,
                    category=category,
                    severity=severity,
                    title=f"{role} → {who} on {place}",
                    detail=detail,
                    suggestion=suggestion,
                    evidence={
                        "scope_level": ra.scope_level,
                        "level": level,
                        "role_type": ra.role_type,
                        "object_kind": "app"
                        if ra.principal_type == "ServicePrincipal"
                        else "group",
                        "app_id": app_id,
                        "owners": _owner_evidence(listed, vias),
                    },
                )
            )

        guests = [o for o in people if _is_guest(o)]
        if guests:
            add(
                "guest_owner",
                HIGH,
                f"A guest from another organisation owns this {'app' if is_app else 'group'} "
                f"({', '.join(o.owner_name or o.owner_id for o in guests)}), and so can use {what}.",
                "Ask whoever invited the guest why they own it; remove them as owner if not needed.",
                guests,
            )
        if is_app:
            apps = [o for o in people if o.owner_type == "ServicePrincipal"]
            humans = [o for o in people if o.owner_type != "ServicePrincipal"]
            if apps:
                add(
                    "app_owned_by_app",
                    HIGH,
                    f"{_count(len(apps), 'other app owns', 'other apps own')} this app: whoever "
                    f"controls {'it' if len(apps) == 1 else 'them'} can add a secret here and use {what}.",
                    "Remove the app as owner, unless it is the pipeline that manages this app.",
                    apps,
                )
            if humans:
                add(
                    "app_owners",
                    HIGH if broad else MEDIUM,
                    f"{_count(len(humans), 'owner', 'owners')} can add a secret to this app and act "
                    f"as it, using {what}.",
                    "Keep as owners only those who must change the app (or none); prefer sign-in "
                    "without a secret (workload identity federation).",
                    humans,
                )
        else:
            add(
                "group_owners",
                HIGH if broad else MEDIUM,
                f"{_count(len(people), 'owner decides', 'owners decide')} who is in this group, "
                f"themselves included, and so who gets {what}.",
                "Keep as owners only the admins who manage the group's members.",
                people,
            )
    return drafts
