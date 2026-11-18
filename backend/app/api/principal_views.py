"""Principal page: access (direct + via group), groups / members, findings,
and the principal's own Activity Log events.

Access comes from :class:`app.core.current_view.CurrentView`, the same
current RA set the scope page and overview use. Activity is matched on
the JWT object-id claim through ``caller_oid_expr`` (indexed by 0021).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.activity import read_events
from app.api.findings import current_finding
from app.api.scope_views import _condition, _origin
from app.core.activity_targets import MGMT_GROUPS, MGMT_GROUPS_NAME
from app.core.advice import NO_ACTION, advise_grant, advise_identity
from app.core.advice import context as advice_context
from app.core.current_view import BROAD_LEVELS, CurrentView, current_view, role_risk_by_arm
from app.core.entra_roles import ENTRA_PLACE
from app.core.findings import finding_sort_key
from app.core.operation_names import names_for
from app.core.scan_coverage import caller_oid_expr, subscription_id_of_scope
from app.core.timeutil import as_utc
from app.core.visibility import vis_of
from app.db.models import (
    ActivityEvent,
    DirectoryAuditEvent,
    GroupMembership,
    Principal,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scope,
)
from app.db.session import get_session
from app.schemas.principals import (
    EntraActivation,
    OperationCount,
    PrincipalAccessRow,
    PrincipalActivity,
    PrincipalGroup,
    PrincipalMember,
    PrincipalPage,
    PrincipalSummary,
    SubscriptionCount,
)
from app.schemas.registry import PrincipalRead

router = APIRouter(tags=["principals"])
# Events, not rows: a daily row stands for ``event_count`` of them (core/activity_compaction.py).
_EVENTS = func.coalesce(func.sum(ActivityEvent.event_count), 0)

_LEVEL_RANK = {
    "directory": -1,
    "mgmt_group": 0,
    "subscription": 1,
    "resource_group": 2,
    "resource": 3,
}
# Bounds an IN list of member object ids; a bigger group is sampled.
_MAX_IDENTITIES = 2000


def _principal_or_404(session: Session, principal_id: str) -> Principal:
    row = session.get(Principal, principal_id.strip().lower())
    if row is None:
        raise HTTPException(status_code=404, detail="no finished check has seen this person or app")
    return row


def _members(session: Session, view: CurrentView, group_id: str) -> list[GroupMembership]:
    """Transitive members from the group's newest membership snapshot."""
    scan_id = view.membership_scan.get(group_id)
    if scan_id is None:
        return []
    return list(
        session.scalars(
            select(GroupMembership)
            .where(GroupMembership.scan_id == scan_id)
            .where(func.lower(GroupMembership.group_id) == group_id)
        ).all()
    )


def _access_row(view: CurrentView, ra: RoleAssignment, via: str | None) -> PrincipalAccessRow:
    sub = subscription_id_of_scope(ra.scope)
    cov = view.coverage_by_ra.get(ra.id) or {}
    return PrincipalAccessRow(
        assignment_id=ra.id,
        scan_id=ra.scan_id,
        scope=ra.scope,
        scope_name=ra.scope_name,
        scope_level=ra.scope_level,
        subscription_id=sub,
        subscription_name=view.name.get(f"/subscriptions/{sub}") if sub else None,
        role_name=ra.role_name,
        role_definition_id=ra.role_definition_id,
        role_type=ra.role_type,
        role_risk=view.risk.get(ra.role_definition_id),
        level=view.capability_of(ra).level,
        is_high_risk=view.is_high_risk(ra),
        source="group" if via else "direct",
        via_group_id=via,
        via_group_name=ra.principal_name if via else None,
        observed_op_count=cov.get("observed_op_count"),
        coverage_pct=cov.get("coverage_pct"),
        open_findings=sum(1 for f in view.findings_by_ra.get(ra.id, ()) if f.status == "open"),
        activated=ra.assignment_type == "Activated",
        until=ra.end_at,
        **_condition(view, ra),
        **_origin(ra),
    )


def _eligible_row(
    view: CurrentView, e: RoleEligibility, via: str | None, risk: dict[str, str | None]
) -> PrincipalAccessRow:
    sub = subscription_id_of_scope(e.scope)
    role_risk = risk.get(e.role_definition_id)
    return PrincipalAccessRow(
        assignment_id=None,
        scan_id=e.scan_id,
        scope=e.scope,
        scope_name=e.scope_name,
        scope_level=e.scope_level,
        subscription_id=sub,
        subscription_name=view.name.get(f"/subscriptions/{sub}") if sub else None,
        role_name=e.role_name,
        role_definition_id=e.role_definition_id,
        role_type=e.role_type,
        role_risk=role_risk,
        level=view.capability_of(e).level,
        is_high_risk=role_risk == "high" and e.scope_level in BROAD_LEVELS,
        source="group" if via else "direct",
        via_group_id=via,
        via_group_name=e.principal_name if via else None,
        eligible=True,
        until=e.end_at,
        **_condition(view, e),
        **_origin(e),
    )


@router.get("/principals/{principal_id}", response_model=PrincipalPage)
def principal_page(
    principal_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    members_limit: Annotated[int, Query(ge=0, le=1000)] = 200,
) -> PrincipalPage:
    row = _principal_or_404(session, principal_id)
    pid = row.id
    view = current_view(session)

    by_principal: dict[str, list[RoleAssignment]] = {}
    for ra in view.ras:
        by_principal.setdefault(ra.principal_id.lower(), []).append(ra)

    group_ids = sorted(view.groups_of({pid}).get(pid, ()))
    held = [(ra, None) for ra in by_principal.get(pid, [])] + [
        (ra, gid) for gid in group_ids for ra in by_principal.get(gid, [])
    ]
    ctx = advice_context(session, view)
    assignments = []
    for ra, via in held:
        row_ = _access_row(view, ra, via)
        verdict = advise_grant(ctx, ra)
        if verdict.rule not in NO_ACTION:
            row_.advice_weight, row_.advice_title = verdict.weight, verdict.title
        assignments.append(row_)
    holders = {pid, *group_ids}
    eligible_held = [
        (e, None if e.principal_id.lower() == pid else e.principal_id.lower())
        for e in view.eligibilities
        if e.principal_id.lower() in holders
    ]
    risk = role_risk_by_arm(session, {e.role_definition_id for e, _ in eligible_held})
    eligible_rows = sorted(
        (_eligible_row(view, e, via, risk) for e, via in eligible_held),
        key=lambda a: (
            _LEVEL_RANK.get(a.scope_level, 9),
            (a.scope_name or a.scope).lower(),
            a.role_name or "",
        ),
    )
    assignments.sort(
        key=lambda a: (
            a.subscription_name or a.subscription_id or "",
            _LEVEL_RANK.get(a.scope_level, 9),
            (a.scope_name or a.scope).lower(),
            a.source != "direct",
            a.role_name or "",
        )
    )

    groups = []
    for gid in group_ids:
        ras = by_principal.get(gid, [])
        g = session.get(Principal, gid)
        groups.append(
            PrincipalGroup(
                group_id=gid,
                group_name=g.display_name if g else (ras[0].principal_name if ras else None),
                scan_id=view.membership_scan[gid],
                assignment_count=len(ras),
                high_risk_count=sum(1 for ra in ras if view.is_high_risk(ra)),
            )
        )
    groups.sort(key=lambda g: (g.group_name or g.group_id).lower())

    members: list[PrincipalMember] | None = None
    members_total = 0
    members_read = False
    if row.principal_type == "Group":
        members_read = pid in view.membership_scan
        rows = _members(session, view, pid)
        members_total = len(rows)
        rows.sort(key=lambda m: ((m.member_name or "~").lower(), m.member_id))
        members = [
            PrincipalMember(
                member_id=m.member_id.lower(), member_name=m.member_name, member_type=m.member_type
            )
            for m in rows[:members_limit]
        ]

    findings = [
        current_finding(ra, ref) for ra, _ in held for ref in view.findings_by_ra.get(ra.id, ())
    ]
    findings.sort(key=lambda f: finding_sort_key(f.severity, f.category, f.title))

    advice = advise_identity(ctx, pid)
    principal = PrincipalRead.model_validate(row)
    principal.assignment_count = sum(1 for a in assignments if a.source == "direct")
    principal.group_count = len(groups)
    vis = vis_of(request)
    if vis is not None and pid not in vis.my_ids:
        # Someone else's page: only what applies to the viewer's places.
        assignments = [a for a in assignments if vis.sees_grant_at(a.scope)]
        eligible_rows = [e for e in eligible_rows if vis.sees_grant_at(e.scope)]
        findings = [f for f in findings if vis.sees_grant_at(f.scope)]
        groups = [g for g in groups if vis.sees_principal(g.group_id)]
        for a in advice.actions:
            seen = [g for g in a.grants if vis.sees_grant_at(g.scope)]
            if len(seen) < len(a.grants):
                a.grants, a.why = seen, ""  # the sentence may name a place they don't see
        advice.actions = [a for a in advice.actions if a.grants]
    activations = _entra_activations(session, pid)
    if vis is not None and not (pid in vis.my_ids or vis.sees_grant_at(ENTRA_PLACE)):
        activations = []
    return PrincipalPage(
        principal=principal,
        summary=PrincipalSummary(
            direct=sum(1 for a in assignments if a.source == "direct"),
            via_group=sum(1 for a in assignments if a.source == "group"),
            high_risk=sum(1 for a in assignments if a.is_high_risk),
            open_findings=sum(1 for f in findings if f.status == "open"),
            subscriptions=len({a.subscription_id for a in assignments if a.subscription_id}),
            eligible=len(eligible_rows),
        ),
        assignments=assignments,
        eligible=eligible_rows,
        groups=groups,
        members=members,
        members_total=members_total,
        members_read=members_read,
        findings=findings,
        advice=advice,
        entra_activations=activations,
    )


def _entra_activations(
    session: Session, principal_id: str, limit: int = 50
) -> list[EntraActivation]:
    """PIM activations of Entra roles by this identity, newest first."""
    rows = session.execute(
        select(DirectoryAuditEvent.occurred_at, DirectoryAuditEvent.role_id)
        .where(DirectoryAuditEvent.kind == "activation")
        .where(DirectoryAuditEvent.action == "add")
        .where(DirectoryAuditEvent.member_id == principal_id.lower())
        .where(func.coalesce(DirectoryAuditEvent.result, "success") == "success")
        .order_by(DirectoryAuditEvent.occurred_at.desc())
        .limit(limit)
    ).all()
    names = dict(
        session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.role_name).where(
                RoleDefinition.role_definition_id.in_({r for _, r in rows if r} or {""})
            )
        ).all()
    )
    return [
        EntraActivation(at=as_utc(at), role_id=rid or "", role_name=names.get(rid or ""))
        for at, rid in rows
    ]


@router.get("/principals/{principal_id}/activity", response_model=PrincipalActivity)
def principal_activity(
    principal_id: str,
    session: Annotated[Session, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=90)] = 90,
    recent_limit: Annotated[int, Query(ge=0, le=200)] = 25,
) -> PrincipalActivity:
    row = _principal_or_404(session, principal_id)
    identities = {row.id}
    if row.principal_type == "Group":
        # Groups never call ARM; their members do. No resolved members → no events.
        view = current_view(session)
        identities = {m.member_id.lower() for m in _members(session, view, row.id)}
    ids = sorted(identities)[:_MAX_IDENTITIES]
    if not ids:
        return PrincipalActivity(
            principal_id=row.id,
            window_days=days,
            identities=0,
            event_count=0,
            operation_count=0,
            newest_at=None,
            callers=[],
            by_subscription=[],
            top_operations=[],
            failed_count=0,
            recent=[],
        )

    since = datetime.now(UTC) - timedelta(days=days)
    oid = caller_oid_expr()
    mine = (ActivityEvent.occurred_at >= since, oid.in_(ids))

    by_op = session.execute(
        select(ActivityEvent.operation_name, _EVENTS, func.max(ActivityEvent.occurred_at))
        .where(*mine)
        .group_by(ActivityEvent.operation_name)
    ).all()
    by_sub = session.execute(
        select(ActivityEvent.subscription_id, _EVENTS, func.max(ActivityEvent.occurred_at))
        .where(*mine)
        .group_by(ActivityEvent.subscription_id)
    ).all()
    callers = Counter(
        dict(
            session.execute(
                select(ActivityEvent.caller, _EVENTS)
                .where(*mine)
                .where(ActivityEvent.caller.is_not(None))
                .group_by(ActivityEvent.caller)
            ).all()
        )
    )
    failed = session.scalar(
        select(_EVENTS)
        .select_from(ActivityEvent)
        .where(*mine)
        .where(ActivityEvent.status == "Failed")
    )
    # Sort on an expression so Postgres can't walk ix_activity_events_occurred_at
    # backwards filtering every row — 13 s for a quiet principal. This way it
    # takes the object-id index and sorts the (few) matches.
    newest_first = func.coalesce(ActivityEvent.occurred_at, ActivityEvent.occurred_at).desc()
    recent = session.scalars(
        select(ActivityEvent).where(*mine).order_by(newest_first).limit(recent_limit)
    ).all()

    sub_names = {
        sid.rsplit("/", 1)[-1]: name
        for sid, name in session.execute(
            select(Scope.id, Scope.name).where(Scope.level == "subscription")
        ).all()
    }
    sub_names[MGMT_GROUPS] = MGMT_GROUPS_NAME

    ops = sorted(by_op, key=lambda r: (-r[1], r[0]))
    op_names = names_for(session, (op for op, _, _ in ops[:15]))
    return PrincipalActivity(
        principal_id=row.id,
        window_days=days,
        identities=len(ids),
        event_count=sum(r[1] for r in by_op),
        operation_count=len(by_op),
        newest_at=max((as_utc(r[2]) for r in by_op if r[2] is not None), default=None),
        callers=[c for c, _ in callers.most_common(5)],
        by_subscription=sorted(
            (
                SubscriptionCount(
                    subscription_id=sub,
                    subscription_name=sub_names.get(sub.lower()),
                    count=n,
                    newest_at=as_utc(newest),
                )
                for sub, n, newest in by_sub
            ),
            key=lambda s: -s.count,
        ),
        top_operations=[
            OperationCount(
                operation=op, display=op_names.get(op), count=n, newest_at=as_utc(newest)
            )
            for op, n, newest in ops[:15]
        ],
        failed_count=failed or 0,
        recent=read_events(session, recent),
    )
