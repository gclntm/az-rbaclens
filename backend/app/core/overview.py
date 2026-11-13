"""Overview (landing page) aggregates over the current RBAC picture."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.capabilities import LEVELS
from app.core.changes import GIVEN_KINDS, REMOVED_KINDS
from app.core.current_view import current_view, grant_id
from app.core.findings import finding_sort_key
from app.core.timeutil import as_utc
from app.db.models import (
    AccessChange,
    ActivityEvent,
    ActivityPull,
    Principal,
    Scan,
    ScanCoverage,
    Scope,
)
from app.schemas.dashboard import (
    AttentionItem,
    Freshness,
    JobRow,
    Overview,
    OverviewKpis,
    StaleScope,
    TopRole,
)


def _changes_30d(session: Session, now: datetime) -> tuple[int, int]:
    """Access given and removed in 30 days, as the changes feed counts them
    (``GET /changes``): one GROUP BY instead of comparing every grant of the
    newest checks with the ones before."""
    rows = session.execute(
        select(AccessChange.kind, func.count())
        .where(AccessChange.occurred_at >= now - timedelta(days=30))
        .where(AccessChange.kind.in_(GIVEN_KINDS + REMOVED_KINDS))
        .group_by(AccessChange.kind)
    ).all()
    given = sum(n for kind, n in rows if kind in GIVEN_KINDS)
    return given, sum(n for kind, n in rows if kind in REMOVED_KINDS)


def build_overview(
    session: Session, attention_limit: int = 8, freshness_days: int = 30
) -> Overview:
    now = datetime.now(UTC)
    view = current_view(session)

    open_refs = [
        (ra, ref)
        for ra in view.ras
        for ref in view.findings_by_ra.get(ra.id, ())
        if ref.status == "open"
    ]
    principal_ids = {ra.principal_id.lower() for ra in view.ras}
    named = {
        pid
        for (pid,) in session.execute(
            select(Principal.id)
            .where(Principal.display_name.isnot(None))
            .where(Principal.id.in_(principal_ids))
        ).all()
    }
    unresolved = {
        ra.principal_id.lower()
        for ra in view.ras
        if not ra.principal_name and ra.principal_id.lower() not in named
    }
    added, removed = _changes_30d(session, now)
    needs_look = [(ra, ref) for ra, ref in open_refs if ref.finding.category != "unused"]
    by_level = Counter(view.strongest_level.values())
    kpis = OverviewKpis(
        assignments=len(view.ras),
        principals=len(principal_ids),
        high_risk_broad=sum(1 for ra in view.ras if view.is_high_risk(ra)),
        unused=sum(1 for _, ref in open_refs if ref.finding.category == "unused"),
        unresolved_principals=len(unresolved),
        open_findings=len(open_refs),
        added_30d=added,
        removed_30d=removed,
        needs_look=len(needs_look),
        by_level={lvl: by_level.get(lvl, 0) for lvl in LEVELS},
    )

    needs_look.sort(
        key=lambda pair: finding_sort_key(
            pair[1].finding.severity, pair[1].finding.category, pair[1].finding.title
        )
    )
    attention = [
        AttentionItem(
            finding_id=ref.finding.id,
            fingerprint=ref.finding.fingerprint,
            scan_id=ref.finding.scan_id,
            category=ref.finding.category,
            severity=ref.finding.severity,
            title=ref.finding.title,
            detail=ref.finding.detail,
            suggestion=ref.finding.suggestion,
            scope=ra.scope,
            scope_name=ra.scope_name,
            scope_level=ra.scope_level,
            principal_id=ra.principal_id,
            principal_name=ra.principal_name,
            role_name=ra.role_name,
            grant_id=grant_id(ra),
        )
        for ra, ref in needs_look[:attention_limit]
    ]

    role_counts: Counter[str] = Counter(ra.role_definition_id for ra in view.ras)
    role_names = {ra.role_definition_id: ra.role_name for ra in view.ras}
    top_roles = [
        TopRole(
            role_name=role_names.get(rd),
            role_definition_id=rd,
            role_risk=view.risk.get(rd),
            count=n,
        )
        for rd, n in role_counts.most_common(8)
    ]

    subs = list(session.scalars(select(Scope).where(Scope.level == "subscription")).all())
    finished = {
        sid: as_utc(f)
        for sid, f in session.execute(
            select(Scan.id, Scan.finished_at).where(
                Scan.id.in_({s.latest_scan_id for s in subs if s.latest_scan_id} or {-1})
            )
        ).all()
    }
    stale: list[StaleScope] = []
    fresh = 0
    for s in subs:
        f = finished.get(s.latest_scan_id) if s.latest_scan_id else None
        age = (now - f).days if f else None
        if age is not None and age <= freshness_days:
            fresh += 1
        else:
            stale.append(StaleScope(id=s.id, name=s.name, level=s.level, age_days=age))
    stale.sort(key=lambda s: (s.age_days is not None, -(s.age_days or 0)))

    names = view.name
    jobs: list[JobRow] = []
    for s in session.scalars(select(Scan).order_by(Scan.created_at.desc()).limit(6)).all():
        jobs.append(
            JobRow(
                kind="snapshot",
                id=s.id,
                target=s.scope,
                target_name=names.get(s.scope),
                status=s.status,
                progress_done=s.progress_done,
                progress_total=s.progress_total,
                progress_message=s.progress_message,
                error=s.error,
                created_at=s.created_at,
                finished_at=s.finished_at,
            )
        )
    for p in session.scalars(
        select(ActivityPull).order_by(ActivityPull.created_at.desc()).limit(6)
    ).all():
        sub_scope = f"/subscriptions/{p.subscription_id.lower()}"
        jobs.append(
            JobRow(
                kind="activity_pull",
                id=p.id,
                target=sub_scope,
                target_name=names.get(sub_scope),
                status=p.status,
                progress_done=p.progress_done,
                progress_total=None,
                progress_message=p.progress_message,
                error=p.error,
                created_at=p.created_at,
                finished_at=p.finished_at,
            )
        )
    jobs.sort(key=lambda j: as_utc(j.created_at) or now, reverse=True)

    contributing = {ra.scan_id for ra in view.ras}
    computed = {
        sid
        for (sid,) in session.execute(
            select(ScanCoverage.scan_id).where(ScanCoverage.scan_id.in_(contributing or {-1}))
        ).all()
    }

    as_of = [
        as_utc(f)
        for (f,) in session.execute(
            select(Scan.finished_at).where(Scan.id.in_(contributing or {-1}))
        ).all()
        if f is not None
    ]
    return Overview(
        findings_pending=sorted(contributing - computed),
        kpis=kpis,
        access_as_of=max(as_of) if as_of else None,
        activity_up_to=as_utc(session.scalar(select(func.max(ActivityEvent.occurred_at)))),
        attention=attention,
        top_roles=top_roles,
        freshness=Freshness(
            window_days=freshness_days,
            subscriptions_total=len(subs),
            subscriptions_fresh=fresh,
            stale=stale[:5],
        ),
        jobs=jobs[:6],
    )
