"""``GET /changes`` — who got or lost access, when, and who did it; ``GET /freshness``
(how fresh the data is, and what keeps it from being kept up to date)."""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.registry import normalise_scope_param
from app.core.changes import GIVEN_KINDS, REMOVED_KINDS
from app.core.current_view import owner_of
from app.core.scan_coverage import latest_done_scan_ids
from app.core.staleness import warnings as stale_warnings
from app.core.timeutil import as_utc
from app.core.visibility import vis_of
from app.db.models import AccessChange, ActivityEvent, JobSchedule, Scan, Scope
from app.db.session import get_session
from app.schemas.changes import (
    ChangeRead,
    ChangesResponse,
    ChangeWeek,
    Freshness,
    LastCheck,
    StaleWarningRead,
)

router = APIRouter(tags=["changes"])

_GRANT_KINDS = ("granted", "removed", "eligible_added", "eligible_removed")
_POWERFUL = ("access", "keys")


def _subtree(session: Session, root: str) -> tuple[set[str], set[str]]:
    """``(known scope ids, ids in root's subtree)``."""
    children: dict[str, list[str]] = defaultdict(list)
    known: set[str] = set()
    for sid, parent in session.execute(select(Scope.id, Scope.parent_id)).all():
        known.add(sid)
        if parent:
            children[parent].append(sid)
    out, stack = {root}, [root]
    while stack:
        for c in children.get(stack.pop(), ()):
            if c not in out:
                out.add(c)
                stack.append(c)
    return known, out


def _read(c: AccessChange) -> ChangeRead:
    return ChangeRead(
        id=c.id,
        occurred_at=as_utc(c.occurred_at),
        kind=c.kind,
        source=c.source,
        scope=c.scope,
        scope_name=c.scope_name,
        scope_level=c.scope_level,
        principal_id=c.principal_id,
        principal_name=c.principal_name,
        principal_type=c.principal_type,
        role_definition_id=c.role_definition_id,
        role_name=c.role_name,
        level=c.level,
        via_group_id=c.via_group_id,
        via_group_name=c.via_group_name,
        actor_id=c.actor_id,
        actor_name=c.actor_name,
        grant_id=c.subject_key if c.kind in _GRANT_KINDS else None,
        check_id=c.first_seen_scan_id,
    )


def _week(d: datetime) -> date:
    day = d.date()
    return day - timedelta(days=day.weekday())


@router.get("/changes", response_model=ChangesResponse)
def list_changes(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    scope: str | None = Query(None, description="A place: changes on it and everything inside."),
    principal: str | None = Query(None, max_length=255),
    days: Annotated[int, Query(ge=1, le=400)] = 30,
    direction: str = Query("all", pattern="^(all|given|removed)$"),
    powerful_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChangesResponse:
    """Newest first. ``given`` = granted, eligible or joined a group;
    ``removed`` = the opposite. Counts and weekly totals cover the whole
    filtered set; ``items`` is one page of it."""
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    stmt = (
        select(AccessChange)
        .where(AccessChange.occurred_at >= since)
        .order_by(AccessChange.occurred_at.desc(), AccessChange.id.desc())
    )
    if principal:
        stmt = stmt.where(AccessChange.principal_id == principal.strip().lower())
    rows = list(session.scalars(stmt).all())
    vis = vis_of(request)
    if vis is not None:
        rows = [r for r in rows if vis.sees_grant(r.scope, r.principal_id)]

    if scope:
        sid = normalise_scope_param(scope)
        known, inside = _subtree(session, sid)
        if sid not in known:
            raise HTTPException(
                status_code=404, detail=f"place {sid} not seen by any finished check"
            )
        rows = [r for r in rows if owner_of(r.scope, known) in inside]

    given = sum(1 for r in rows if r.kind in GIVEN_KINDS)
    removed = len(rows) - given
    powerful = sum(1 for r in rows if r.level in _POWERFUL)
    weeks: dict[date, Counter[str]] = defaultdict(Counter)
    for r in rows:
        weeks[_week(as_utc(r.occurred_at))]["given" if r.kind in GIVEN_KINDS else "removed"] += 1

    if direction == "given":
        rows = [r for r in rows if r.kind in GIVEN_KINDS]
    elif direction == "removed":
        rows = [r for r in rows if r.kind in REMOVED_KINDS]
    if powerful_only:
        rows = [r for r in rows if r.level in _POWERFUL]

    return ChangesResponse(
        since=since,
        until=until,
        total=len(rows),
        given=given,
        removed=removed,
        powerful=powerful,
        weeks=[
            ChangeWeek(week_start=w, given=c["given"], removed=c["removed"])
            for w, c in sorted(weeks.items())
        ],
        items=[_read(r) for r in rows[offset : offset + limit]],
    )


@router.get("/freshness", response_model=Freshness)
def freshness(session: Annotated[Session, Depends(get_session)], request: Request) -> Freshness:
    """How current the data is. Someone who is not an admin gets the times only: the
    last check's error and the warnings name the tenant's places and point at admin pages."""
    admin = vis_of(request) is None
    pointers = {p for (p,) in session.execute(select(Scope.latest_scan_id)).all() if p}
    current = pointers or latest_done_scan_ids(session)
    as_of = session.scalar(select(func.max(Scan.finished_at)).where(Scan.id.in_(current or {-1})))
    daily = session.scalars(
        select(JobSchedule)
        .where(JobSchedule.kind == "snapshot", JobSchedule.at_hour.is_not(None))
        .order_by(JobSchedule.id)
        .limit(1)
    ).first()
    last = session.get(Scan, daily.last_job_id) if daily and daily.last_job_id else None
    return Freshness(
        access_as_of=as_utc(as_of),
        activity_up_to=as_utc(session.scalar(select(func.max(ActivityEvent.occurred_at)))),
        daily_check_at_hour=daily.at_hour if daily else None,
        timezone=os.environ.get("TZ") or "UTC",
        next_check_at=as_utc(daily.next_run_at) if daily and daily.enabled else None,
        last_check=LastCheck(
            id=last.id, status=last.status, finished_at=as_utc(last.finished_at), error=last.error
        )
        if last and admin
        else None,
        warnings=[
            StaleWarningRead(
                kind=w.kind, text=w.text, names=w.names, path=w.path, severity=w.severity
            )
            for w in stale_warnings(session)
        ]
        if admin
        else [],
    )
