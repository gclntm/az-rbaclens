"""``GET /scopes/capabilities`` — who can do what at a scope, in plain levels.

Every identity whose access applies here (assigned here or inherited from
a parent) appears once, at the most powerful level any of its grants
reaches (``app.core.capabilities``). Grants on child scopes are summarised
separately as "inside": child scopes that give someone *more* than they
have here.

Fixed path with the scope in a query parameter; included before the
registry router like ``scope_views``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.scope_views import _classified, _condition, _origin, _require_scope
from app.core.capabilities import LEVEL_RANK, UNKNOWN, Capability, classify
from app.core.current_view import CurrentView, current_view, owner_of
from app.db.models import (
    GroupMembership,
    Principal,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scan,
)
from app.db.session import get_session
from app.schemas.capabilities import (
    CapabilityGrant,
    CapabilityIdentity,
    CapabilityLevelGroup,
    InsideScope,
    PimSummary,
    ScopeCapabilities,
    SnapshotRef,
)

router = APIRouter(prefix="/scopes", tags=["scope-page"])

_KIND = {
    "User": "person",
    "Group": "group",
    "ForeignGroup": "group",
    "ServicePrincipal": "app",
    "MSI": "app",
    "Application": "app",
}
_SHOWN_LEVELS = ("access", "keys", "change", "data", "view")


def _guid(role_definition_id: str) -> str:
    return role_definition_id.rsplit("/", 1)[-1].lower()


def _capabilities(session: Session, ras: list[RoleAssignment]) -> dict[str, Capability]:
    """``{role GUID: Capability}`` for every role in ``ras``."""
    guids = {_guid(ra.role_definition_id) for ra in ras}
    out = dict.fromkeys(guids, UNKNOWN)
    if guids:
        for guid, permissions in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions).where(
                func.lower(RoleDefinition.role_definition_id).in_(guids)
            )
        ).all():
            out[guid.lower()] = classify(permissions)
    return out


def _strongest(levels) -> str:
    return min(levels, key=lambda lvl: LEVEL_RANK[lvl])


@router.get("/capabilities", response_model=ScopeCapabilities)
def scope_capabilities(
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(..., description="Scope id (ARM path, any case)."),
    inside_limit: Annotated[int, Query(ge=0, le=200)] = 30,
) -> ScopeCapabilities:
    sid = _require_scope(session, scope)
    view = current_view(session)
    classified = list(_classified(view, sid))
    ancestors, subtree = set(view.ancestors(sid)), view.subtree(sid)
    eligible_here = [
        e
        for e in view.eligibilities
        if view.classify_scope(e.scope, sid, ancestors, subtree) in ("direct", "inherited")
    ]
    caps = _capabilities(session, [ra for ra, _ in classified] + eligible_here)

    def cap_of(row) -> Capability:
        return caps[_guid(row.role_definition_id)]

    # ---- identities whose access applies here --------------------------------
    effective: dict[str, list[RoleAssignment]] = defaultdict(list)
    below: list[RoleAssignment] = []
    for ra, source in classified:
        if source == "below":
            below.append(ra)
        else:
            effective[ra.principal_id.lower()].append(ra)
    eligible: dict[str, list[RoleEligibility]] = defaultdict(list)
    for e in eligible_here:
        eligible[e.principal_id.lower()].append(e)

    pids = set(effective) | set(eligible)
    registry = {
        p.id: p for p in session.scalars(select(Principal).where(Principal.id.in_(pids or {""})))
    }
    first_row = {pid: (effective.get(pid) or eligible[pid])[0] for pid in pids}
    group_ids = [pid for pid in pids if _KIND.get(first_row[pid].principal_type) == "group"]
    member_counts, people_via_groups = _group_members(session, view, group_ids)

    def grant(row, *, is_eligible: bool) -> CapabilityGrant:
        return CapabilityGrant(
            assignment_id=None if is_eligible else row.id,
            role_name=row.role_name,
            role_definition_id=row.role_definition_id,
            level=cap_of(row).level,
            how="here" if row.scope.lower() == sid else "inherited",
            from_scope_id=row.scope.lower(),
            from_scope_name=view.name.get(row.scope.lower()) or row.scope_name,
            from_scope_level=row.scope_level,
            eligible=is_eligible,
            activated=not is_eligible and row.assignment_type == "Activated",
            until=row.end_at,
            **_condition(view, row),
            **_origin(row),
        )

    # Within a level an unconditioned grant comes first: it is the one that
    # defines what the identity can really do, so a row never shows a
    # narrower, conditioned grant as the deciding one.
    def order(g: CapabilityGrant):
        return (
            LEVEL_RANK[g.level],
            g.condition_kind is not None,
            g.how != "here",
            g.role_name or "",
        )

    identities: list[CapabilityIdentity] = []
    for pid in pids:
        first = first_row[pid]
        grants = sorted((grant(ra, is_eligible=False) for ra in effective.get(pid, [])), key=order)
        eligible_grants = sorted(
            (grant(e, is_eligible=True) for e in eligible.get(pid, [])), key=order
        )
        active_level = grants[0].level if grants else None
        eligible_level = eligible_grants[0].level if eligible_grants else None
        on_request = eligible_level is not None and (
            active_level is None or LEVEL_RANK[eligible_level] < LEVEL_RANK[active_level]
        )
        rows_caps = sorted(
            (cap_of(r) for r in [*effective.get(pid, []), *eligible.get(pid, [])]),
            key=lambda c: LEVEL_RANK[c.level],
        )
        rp = registry.get(pid)
        kind = _KIND.get(first.principal_type, "other")
        identities.append(
            CapabilityIdentity(
                id=pid,
                name=(rp.display_name if rp else None) or first.principal_name,
                principal_type=first.principal_type,
                kind=kind,
                level=eligible_level if on_request else active_level,
                data=any(c.data for c in rows_caps),
                data_write=any(c.data_write for c in rows_caps),
                keys_for=list(dict.fromkeys(k for c in rows_caps for k in c.keys_for)),
                grants=grants,
                eligible_grants=eligible_grants,
                on_request=on_request,
                member_count=member_counts.get(pid) if kind == "group" else None,
                last_activity_at=rp.last_activity_at if rp and kind != "group" else None,
            )
        )

    kind_order = {"person": 0, "group": 1, "app": 2, "other": 3}
    identities.sort(key=lambda i: (kind_order[i.kind], (i.name or i.id).lower()))
    levels = [
        CapabilityLevelGroup(
            level=lvl,
            identities=[i for i in identities if i.level == lvl],
            people=sum(1 for i in identities if i.level == lvl and i.kind == "person"),
            groups=sum(1 for i in identities if i.level == lvl and i.kind == "group"),
            apps=sum(1 for i in identities if i.level == lvl and i.kind in ("app", "other")),
            on_request=sum(1 for i in identities if i.level == lvl and i.on_request),
        )
        for lvl in (*_SHOWN_LEVELS, "unknown")
        if lvl != "unknown" or any(i.level == "unknown" for i in identities)
    ]
    pim = _pim_summary(view, sid, classified, eligible_here, identities)

    # ---- child scopes that grant more than applies here -----------------------
    here_level = {i.id: i.level for i in identities}
    at_child: dict[str, dict[str, str]] = defaultdict(dict)  # child -> {pid: strongest level}
    child_meta: dict[str, tuple[str | None, str]] = {}
    for ra in below:
        child = ra.scope.lower()
        child_meta.setdefault(child, (view.name.get(child) or ra.scope_name, ra.scope_level))
        pid, lvl = ra.principal_id.lower(), cap_of(ra).level
        prev = at_child[child].get(pid)
        at_child[child][pid] = lvl if prev is None else _strongest((prev, lvl))
    inside: list[InsideScope] = []
    for child, granted in at_child.items():
        extra = {
            pid: lvl
            for pid, lvl in granted.items()
            if pid not in here_level or LEVEL_RANK[lvl] < LEVEL_RANK[here_level[pid]]
        }
        if not extra:
            continue
        by_level: dict[str, int] = defaultdict(int)
        for lvl in extra.values():
            by_level[lvl] += 1
        name, level = child_meta[child]
        inside.append(
            InsideScope(
                scope_id=child,
                name=name,
                level=level,
                extra_identities=len(extra),
                top_level=_strongest(extra.values()),
                by_level=dict(by_level),
            )
        )
    inside.sort(
        key=lambda s: (LEVEL_RANK[s.top_level], -s.extra_identities, (s.name or s.scope_id).lower())
    )

    return ScopeCapabilities(
        scope_id=sid,
        identities=len(identities),
        people=sum(1 for i in identities if i.kind == "person"),
        groups=len(group_ids),
        apps=sum(1 for i in identities if i.kind in ("app", "other")),
        people_via_groups=people_via_groups,
        pim=pim,
        groups_unresolved=sum(1 for g in group_ids if member_counts.get(g) is None),
        levels=levels,
        inside=inside[:inside_limit],
        inside_total=len(inside),
    )


def _pim_summary(view: CurrentView, sid: str, classified, eligible_here, identities) -> PimSummary:
    """Combine the PIM status of every snapshot behind this scope's access."""
    scan_ids = {ra.scan_id for ra, src in classified if src != "below"} | {
        e.scan_id for e in eligible_here
    }
    owner = owner_of(sid, view.pointer)
    if owner and view.pointer.get(owner):
        scan_ids.add(view.pointer[owner])
    statuses = [view.eligibility_status.get(i, (None, None)) for i in scan_ids]
    unchecked_ids = sorted(
        i for i in scan_ids if view.eligibility_status.get(i, (None, None))[0] is None
    )
    unchecked = [
        SnapshotRef(id=s.id, scope_id=s.scope.lower(), name=view.name.get(s.scope.lower()))
        for s in view.session.scalars(select(Scan).where(Scan.id.in_(unchecked_ids or [-1]))).all()
    ]
    errors = [err for _st, err in statuses if err]
    if not statuses or any(st is None for st, _ in statuses):
        status = "not_checked"
    elif any(st == "failed" for st, _ in statuses):
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "read"
    return PimSummary(
        status=status,
        error=errors[0] if errors else None,
        unchecked=unchecked,
        eligible_identities=sum(1 for i in identities if i.eligible_grants),
        time_bound_grants=sum(1 for i in identities for g in i.grants if g.until),
        permanent_privileged=sum(
            1
            for i in identities
            if i.level in ("access", "keys")
            and not i.on_request
            and i.grants
            and i.grants[0].until is None
        ),
    )


def _group_members(
    session: Session, view: CurrentView, group_ids: list[str]
) -> tuple[dict[str, int], int]:
    """``({group: member count}, distinct people across them)`` from each
    group's newest membership snapshot; unresolved groups are absent."""
    scans = {g: view.membership_scan[g] for g in group_ids if g in view.membership_scan}
    if not scans:
        return {}, 0
    counts: dict[str, int] = dict.fromkeys(scans, 0)
    people: set[str] = set()
    for gid, mid, mtype, scan_id in session.execute(
        select(
            GroupMembership.group_id,
            GroupMembership.member_id,
            GroupMembership.member_type,
            GroupMembership.scan_id,
        ).where(GroupMembership.scan_id.in_(set(scans.values())))
    ).all():
        g = gid.lower()
        if scans.get(g) != scan_id:
            continue
        counts[g] += 1
        if mtype == "User":
            people.add(mid.lower())
    return counts, len(people)
