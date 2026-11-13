"""Scope page data: assignments (direct / inherited / below), findings, history.

Fixed paths with the scope in a query parameter, so they never collide
with ``GET /scopes/{scope_id:path}``: ARM ids always start with
``subscriptions/`` or ``providers/``. This router must be included
before the registry router.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.registry import normalise_scope_param
from app.core.current_view import CurrentView, current_view, grant_id
from app.core.findings import finding_sort_key
from app.core.operation_names import names_for
from app.db.models import (
    ActivityEvent,
    ActivityPull,
    RoleAssignment,
    RoleEligibility,
    Scan,
    ScanCoverage,
    Scope,
)
from app.db.session import get_session
from app.schemas.findings import FindingRead, FindingsSummary
from app.schemas.registry import (
    ActiveAssignment,
    AssignmentFindingRef,
    PullRef,
    ScopeActivity,
    ScopeAssignmentRow,
    ScopeAssignments,
    ScopeEligibleRow,
    ScopeFindingRead,
    ScopeFindings,
    ScopeSnapshot,
    SubscriptionActivity,
)

router = APIRouter(prefix="/scopes", tags=["scope-page"])

_SOURCE_ORDER = {"direct": 0, "inherited": 1, "below": 2}


def _require_scope(session: Session, raw: str) -> str:
    sid = normalise_scope_param(raw)
    if session.get(Scope, sid) is None:
        raise HTTPException(status_code=404, detail=f"place {sid} not seen by any finished check")
    return sid


def _condition(view: CurrentView, ra) -> dict[str, str | None]:
    """Condition fields for a RoleAssignment or RoleEligibility."""
    c = view.condition_of(ra)
    return {"condition_kind": c.kind if c else None, "condition_text": c.text if c else None}


def _origin(ra) -> dict[str, Any]:
    """Origin fields for a RoleAssignment, or a RoleEligibility (its start, no author)."""
    if isinstance(ra, RoleEligibility):
        return {"since": ra.start_at}
    return {
        "grant_id": grant_id(ra),
        "since": ra.created_on,
        "given_by_id": ra.created_by,
        "given_by_name": ra.created_by_name,
        "given_by_type": ra.created_by_type,
    }


def _classified(view: CurrentView, sid: str):
    ancestors = set(view.ancestors(sid))
    subtree = view.subtree(sid)
    for ra in view.ras:
        source = view.classify(ra, sid, ancestors, subtree)
        if source is not None:
            yield ra, source


@router.get("/assignments", response_model=ScopeAssignments)
def scope_assignments(
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(..., description="Scope id (ARM path, any case)."),
    source: str | None = Query(None, pattern="^(direct|inherited|below)$"),
) -> ScopeAssignments:
    """Every current RA that applies at or under ``scope``.

    ``direct`` — granted exactly at the scope; ``inherited`` — granted at
    an ancestor and therefore effective here; ``below`` — granted on a
    descendant. ``counts`` always covers all three; ``source`` narrows
    ``rows`` only. Sorted direct → inherited → below, high-risk first.
    """
    sid = _require_scope(session, scope)
    view = current_view(session)
    counts: Counter[str] = Counter()
    rows: list[ScopeAssignmentRow] = []
    coverage = view.coverage_by_ra
    for ra, src in _classified(view, sid):
        counts[src] += 1
        if source and src != source:
            continue
        cov = coverage.get(ra.id) or {}
        rows.append(
            ScopeAssignmentRow(
                assignment_id=ra.id,
                scan_id=ra.scan_id,
                source=src,
                scope=ra.scope,
                scope_name=ra.scope_name,
                scope_level=ra.scope_level,
                principal_id=ra.principal_id,
                principal_name=ra.principal_name,
                principal_type=ra.principal_type,
                role_name=ra.role_name,
                role_definition_id=ra.role_definition_id,
                role_type=ra.role_type,
                role_risk=view.risk.get(ra.role_definition_id),
                coverage_pct=cov.get("coverage_pct"),
                granted_pattern_count=cov.get("granted_pattern_count"),
                observed_op_count=cov.get("observed_op_count"),
                member_count=cov.get("member_count"),
                activated=ra.assignment_type == "Activated",
                until=ra.end_at,
                **_condition(view, ra),
                **_origin(ra),
                covered_by_count=len(cov.get("covered_by") or []),
                findings=[
                    AssignmentFindingRef(
                        finding_id=ref.finding.id,
                        fingerprint=ref.finding.fingerprint,
                        category=ref.finding.category,
                        severity=ref.finding.severity,
                        status=ref.status,
                    )
                    for ref in view.findings_by_ra.get(ra.id, ())
                ],
            )
        )
    rows.sort(
        key=lambda r: (
            _SOURCE_ORDER[r.source],
            0 if view.is_high_risk(view.by_id[r.assignment_id]) else 1,
            (r.principal_name or r.principal_id).lower(),
            r.role_name or "",
        )
    )
    ancestors, subtree = set(view.ancestors(sid)), view.subtree(sid)
    eligible = []
    for e in view.eligibilities:
        src = view.classify_scope(e.scope, sid, ancestors, subtree)
        if src is None or (source and src != source):
            continue
        eligible.append(
            ScopeEligibleRow(
                source=src,
                scope=e.scope,
                scope_name=e.scope_name,
                scope_level=e.scope_level,
                principal_id=e.principal_id,
                principal_name=e.principal_name,
                principal_type=e.principal_type,
                role_name=e.role_name,
                role_definition_id=e.role_definition_id,
                member_type=e.member_type,
                until=e.end_at,
                **_condition(view, e),
                **_origin(e),
            )
        )
    eligible.sort(
        key=lambda r: (_SOURCE_ORDER[r.source], (r.principal_name or r.principal_id).lower())
    )
    return ScopeAssignments(
        scope_id=sid,
        counts={k: counts.get(k, 0) for k in ("direct", "inherited", "below")},
        rows=rows,
        eligible=eligible,
    )


@router.get("/findings", response_model=ScopeFindings)
def scope_findings(
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(...),
    status: str | None = Query(None, pattern="^(open|reviewed|accepted_risk|snoozed|resolved)$"),
    severity: str | None = Query(None, pattern="^(high|medium|low)$"),
) -> ScopeFindings:
    """Findings on every current RA that applies at or under ``scope``.

    ``summary`` covers the full set; ``status`` / ``severity`` narrow
    ``findings`` only.
    """
    sid = _require_scope(session, scope)
    view = current_view(session)
    items: list[ScopeFindingRead] = []
    for ra, src in _classified(view, sid):
        for ref in view.findings_by_ra.get(ra.id, ()):
            base = FindingRead.model_validate(ref.finding)
            items.append(
                ScopeFindingRead(
                    **base.model_dump(exclude={"status", "decision"}),
                    status=ref.status,
                    decision=ref.decision,
                    source=src,
                    scope=ra.scope,
                    scope_name=ra.scope_name,
                    scope_level=ra.scope_level,
                    principal_id=ra.principal_id,
                    principal_name=ra.principal_name,
                    principal_type=ra.principal_type,
                    role_name=ra.role_name,
                )
            )
    items.sort(key=lambda f: finding_sort_key(f.severity, f.category, f.title))
    summary = FindingsSummary(
        total=len(items),
        open=sum(1 for f in items if f.status == "open"),
        by_severity=dict(Counter(f.severity for f in items)),
        by_category=dict(Counter(f.category for f in items)),
        by_status=dict(Counter(f.status for f in items)),
    )
    if status:
        items = [f for f in items if f.status == status]
    if severity:
        items = [f for f in items if f.severity == severity]
    return ScopeFindings(scope_id=sid, summary=summary, findings=items)


@router.get("/history", response_model=list[ScopeSnapshot])
def scope_history(
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
) -> list[ScopeSnapshot]:
    """Snapshots that covered ``scope``: scans rooted at it or at an ancestor.

    Newest first. ``is_current`` marks the snapshot that owns the scope
    now; ``previous_done_id`` is the prior completed scan of the same
    root (for a diff link).
    """
    sid = _require_scope(session, scope)
    view = current_view(session)
    roots = [sid, *view.ancestors(sid)]
    scans = list(
        session.scalars(
            select(Scan)
            .where(Scan.scope.in_(roots))
            .order_by(Scan.created_at.desc(), Scan.id.desc())
            .limit(limit)
        ).all()
    )
    ids = [s.id for s in scans]
    counts = dict(
        session.execute(
            select(RoleAssignment.scan_id, func.count())
            .where(RoleAssignment.scan_id.in_(ids or [-1]))
            .group_by(RoleAssignment.scan_id)
        ).all()
    )
    # Previous completed scan per root, for every scan in the page.
    done_by_root: dict[str, list[Scan]] = {}
    for s in session.scalars(
        select(Scan)
        .where(Scan.scope.in_(roots))
        .where(Scan.status == "done")
        .order_by(Scan.created_at, Scan.id)
    ).all():
        done_by_root.setdefault(s.scope, []).append(s)

    def previous_done(scan: Scan) -> int | None:
        prev = None
        for s in done_by_root.get(scan.scope, []):
            if s.id == scan.id:
                return prev
            if (s.created_at, s.id) < (scan.created_at, scan.id):
                prev = s.id
        return prev

    current = view.pointer.get(sid)
    return [
        ScopeSnapshot(
            id=s.id,
            scope=s.scope,
            scope_name=view.name.get(s.scope),
            scope_level=s.scope_level,
            status=s.status,
            created_at=s.created_at,
            finished_at=s.finished_at,
            error=s.error,
            role_assignment_count=counts.get(s.id, 0),
            is_current=s.id == current,
            previous_done_id=previous_done(s),
        )
        for s in scans
    ]


def _subscriptions_for(view: CurrentView, sid: str) -> list[str]:
    """Subscriptions whose Activity Log describes ``sid``: itself, its
    enclosing subscription, or every subscription below a management group."""
    from app.core.scan_coverage import subscription_id_of_scope

    own = subscription_id_of_scope(sid)
    if own:
        return [f"/subscriptions/{own}"]
    return sorted(s for s in view.subtree(sid) if view.level.get(s) == "subscription")


def _pull_ref(pull: ActivityPull | None) -> PullRef | None:
    if pull is None:
        return None
    return PullRef(
        id=pull.id,
        status=pull.status,
        start_time=pull.start_time,
        end_time=pull.end_time,
        finished_at=pull.finished_at,
        events_persisted=pull.events_persisted,
    )


@router.get("/activity", response_model=ScopeActivity)
def scope_activity(
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(...),
    most_active_limit: int = Query(10, ge=1, le=50),
) -> ScopeActivity:
    """What the Activity Log says about ``scope``.

    Per subscription: event volume in the last 90 days and pull freshness.
    From the owning snapshot's cached coverage: attribution totals and the
    callers active in its subscriptions that hold no visible RA
    ("unattributed activity"). Plus the most active current assignments
    that apply here.
    """
    sid = _require_scope(session, scope)
    view = current_view(session)
    sub_scopes = _subscriptions_for(view, sid)
    sub_ids = [s.rsplit("/", 1)[-1] for s in sub_scopes]

    since = datetime.now(UTC) - timedelta(days=90)
    volume = {
        sub: (n, newest)
        for sub, n, newest in session.execute(
            # A daily row stands for ``event_count`` events (core/activity_compaction.py).
            select(
                ActivityEvent.subscription_id,
                func.sum(ActivityEvent.event_count),
                func.max(ActivityEvent.occurred_at),
            )
            .where(ActivityEvent.subscription_id.in_(sub_ids or ["-"]))
            .where(ActivityEvent.occurred_at >= since)
            .group_by(ActivityEvent.subscription_id)
        ).all()
    }
    subscriptions: list[SubscriptionActivity] = []
    for sub_scope, sub in zip(sub_scopes, sub_ids, strict=True):
        last = session.scalars(
            select(ActivityPull)
            .where(func.lower(ActivityPull.subscription_id) == sub)
            .order_by(ActivityPull.created_at.desc(), ActivityPull.id.desc())
            .limit(1)
        ).first()
        last_ok = session.scalars(
            select(ActivityPull)
            .where(func.lower(ActivityPull.subscription_id) == sub)
            .where(ActivityPull.status == "done")
            .order_by(ActivityPull.end_time.desc(), ActivityPull.id.desc())
            .limit(1)
        ).first()
        n, newest = volume.get(sub, (0, None))
        subscriptions.append(
            SubscriptionActivity(
                id=sub_scope,
                name=view.name.get(sub_scope),
                events_90d=n,
                newest_event_at=newest,
                last_pull=_pull_ref(last),
                last_successful_pull=_pull_ref(last_ok),
            )
        )

    snapshot_id = view.pointer.get(sid)
    cache = session.get(ScanCoverage, snapshot_id) if snapshot_id else None
    payload = cache.payload if cache else {}

    active: list[ActiveAssignment] = []
    coverage = view.coverage_by_ra
    for ra, src in _classified(view, sid):
        row = coverage.get(ra.id)
        if not row or not row.get("top_operations"):
            continue
        ops = row["top_operations"]
        active.append(
            ActiveAssignment(
                assignment_id=ra.id,
                principal_id=ra.principal_id,
                principal_name=ra.principal_name,
                principal_type=ra.principal_type,
                role_name=ra.role_name,
                scope=ra.scope,
                scope_name=ra.scope_name,
                source=src,
                observed_op_count=row.get("observed_op_count") or 0,
                event_count=sum(int(o.get("count") or 0) for o in ops),
                top_operations=ops[:3],
            )
        )
    active.sort(key=lambda a: (-a.event_count, (a.principal_name or a.principal_id).lower()))
    active = active[:most_active_limit]
    op_names = names_for(
        session, (o.get("operation_name") for a in active for o in a.top_operations)
    )
    for a in active:
        a.top_operations = [
            {**o, "display": op_names.get(o.get("operation_name"))} for o in a.top_operations
        ]

    return ScopeActivity(
        scope_id=sid,
        subscriptions=subscriptions,
        snapshot_id=snapshot_id if cache else None,
        computed_at=cache.computed_at if cache else None,
        window_days=cache.window_days if cache else None,
        events_considered=payload.get("events_considered"),
        events_attributed=payload.get("events_attributed"),
        events_unattributed=payload.get("events_unattributed"),
        unattributed_callers=payload.get("unattributed_callers") or [],
        most_active=active,
    )
