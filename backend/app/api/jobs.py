"""Jobs page API: recent scans + pulls in one list, and schedule CRUD.

Manual one-off jobs keep using ``POST /iam-access/scans`` and
``POST /activity/pulls``; this module adds the recurring side
(``app.core.scheduler``) and a merged view of what ran.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import coverage as coverage_mod
from app.core import scheduler
from app.core.config import settings
from app.core.default_schedules import decline
from app.core.job_errors import explain
from app.db.models import ActivityPull, JobSchedule, Scan
from app.db.session import get_session
from app.schemas.jobs import (
    CoverageRead,
    JobRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    SecondPassRead,
    SubscriptionCoverageRead,
)
from app.tools.scan_walk import unread_scopes

router = APIRouter(tags=["jobs"])


def _job_statuses(session: Session, schedules: list[JobSchedule]) -> dict[tuple[str, int], str]:
    """Status of each schedule's last job, keyed by ``(kind, job_id)``."""
    ids = {
        kind: [s.last_job_id for s in schedules if s.kind == kind and s.last_job_id]
        for kind in scheduler.KINDS
    }
    out: dict[tuple[str, int], str] = {}
    if ids["snapshot"]:
        for job_id, status in session.execute(
            select(Scan.id, Scan.status).where(Scan.id.in_(ids["snapshot"]))
        ):
            out[("snapshot", job_id)] = status
    if ids["activity_pull"]:
        for job_id, status in session.execute(
            select(ActivityPull.id, ActivityPull.status).where(
                ActivityPull.id.in_(ids["activity_pull"])
            )
        ):
            out[("activity_pull", job_id)] = status
    return out


def _read(schedule: JobSchedule, statuses: dict[tuple[str, int], str]) -> ScheduleRead:
    out = ScheduleRead.model_validate(schedule)
    out.last_error_summary = explain(schedule.last_error)
    if schedule.last_job_id is not None:
        out.last_job_status = statuses.get((schedule.kind, schedule.last_job_id))
        if out.last_job_status == "done" and schedule.settled_job_id != schedule.last_job_id:
            # Done, but the scheduler's next tick (up to a minute) has not
            # recorded it yet: show what it will record rather than the
            # previous failure next to a "done".
            out.failures = 0
            out.last_error = None
            out.last_error_summary = None
    return out


def _one(session: Session, schedule_id: int) -> JobSchedule:
    schedule = session.get(JobSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    return schedule


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    kind: Annotated[Literal["snapshot", "activity_pull"] | None, Query()] = None,
    status: Annotated[Literal["pending", "running", "done", "failed"] | None, Query()] = None,
) -> list[JobRead]:
    """The most recent scans and activity pulls, newest first.

    ``kind`` and ``status`` filter before the limit, so ``status=failed``
    returns the newest failures however many runs succeeded since.
    """
    scan_q = select(Scan).order_by(Scan.id.desc()).limit(limit)
    pull_q = select(ActivityPull).order_by(ActivityPull.id.desc()).limit(limit)
    if status:
        scan_q, pull_q = (
            scan_q.where(Scan.status == status),
            pull_q.where(ActivityPull.status == status),
        )
    scans = [] if kind == "activity_pull" else session.scalars(scan_q).all()
    pulls = [] if kind == "snapshot" else session.scalars(pull_q).all()
    jobs = [
        JobRead(
            kind="snapshot",
            id=s.id,
            target=s.scope,
            status=s.status,
            progress_message=s.progress_message,
            error=s.error,
            error_summary=explain(s.error),
            origin=s.origin,
            schedule_id=s.schedule_id,
            created_at=s.created_at,
            started_at=s.started_at,
            finished_at=s.finished_at,
            unread=len(unread_scopes(s.hierarchy)),
        )
        for s in scans
    ] + [
        JobRead(
            kind="activity_pull",
            id=p.id,
            target=p.subscription_id,
            status=p.status,
            progress_message=p.progress_message,
            error=p.error,
            error_summary=explain(p.error),
            origin=p.origin,
            schedule_id=p.schedule_id,
            created_at=p.created_at,
            started_at=p.started_at,
            finished_at=p.finished_at,
            window_start=p.start_time,
            window_end=p.end_time,
            events_persisted=p.events_persisted,
        )
        for p in pulls
    ]
    jobs.sort(key=lambda j: scheduler.as_utc(j.created_at), reverse=True)
    return jobs[:limit]


@router.get("/activity/coverage", response_model=CoverageRead)
def activity_coverage(session: Annotated[Session, Depends(get_session)]) -> CoverageRead:
    """Per subscription: collected up to when, how far behind, and the days left
    before Azure's 90 days run out; plus the nightly second pass."""
    rows = coverage_mod.coverage(session)
    summary = coverage_mod.second_pass_summary(rows)
    return CoverageRead(
        azure_keeps_days=coverage_mod.AZURE_KEEPS_DAYS,
        at_risk_days_left=coverage_mod.AT_RISK_DAYS_LEFT,
        subscriptions=[
            SubscriptionCoverageRead(
                subscription_id=c.subscription_id,
                name=c.name,
                schedule_id=c.schedule_id,
                enabled=c.enabled,
                collecting=c.collecting,
                covered_until=c.covered_until,
                behind_hours=c.behind_hours,
                days_left=c.days_left,
                state=c.state,
                second_pass=SecondPassRead(
                    status=c.second_pass.status,
                    finished_at=c.second_pass.finished_at,
                    late_events=c.second_pass.late_events,
                )
                if c.second_pass
                else None,
            )
            for c in rows
        ],
        second_pass_hours=settings.second_pass_hours,
        second_pass_finished_at=summary.last_finished_at,
        second_pass_subscriptions=summary.subscriptions,
        second_pass_late_events=summary.late_events,
        second_pass_failed=summary.failed,
    )


@router.get("/schedules", response_model=list[ScheduleRead])
def list_schedules(session: Annotated[Session, Depends(get_session)]) -> list[ScheduleRead]:
    schedules = list(
        session.scalars(select(JobSchedule).order_by(JobSchedule.kind, JobSchedule.target))
    )
    statuses = _job_statuses(session, schedules)
    return [_read(s, statuses) for s in schedules]


@router.post("/schedules", response_model=ScheduleRead, status_code=201)
def create_schedule(
    payload: ScheduleCreate, session: Annotated[Session, Depends(get_session)]
) -> ScheduleRead:
    try:
        target = scheduler.normalize_target(payload.kind, payload.target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = datetime.now(UTC)
    schedule = JobSchedule(
        kind=payload.kind,
        target=target,
        every_hours=payload.every_hours,
        at_hour=payload.at_hour,
        enabled=payload.enabled,
        initial_window_days=payload.initial_window_days,
        next_run_at=now
        if payload.start_now
        else scheduler.next_run(payload.every_hours, payload.at_hour, now),
    )
    session.add(schedule)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="a schedule for this target already exists"
        ) from exc
    session.refresh(schedule)
    return _read(schedule, {})


@router.patch("/schedules/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> ScheduleRead:
    schedule = _one(session, schedule_id)
    every = payload.every_hours if payload.every_hours is not None else schedule.every_hours
    if payload.at_hour is None:
        hour = schedule.at_hour if every == 24 else None
    else:
        hour = None if payload.at_hour == -1 else payload.at_hour
    if hour is not None and every != 24:
        raise HTTPException(status_code=400, detail="at_hour needs every_hours = 24")
    if every != schedule.every_hours or hour != schedule.at_hour:
        schedule.every_hours = every
        schedule.at_hour = hour
        if hour is not None:
            schedule.next_run_at = scheduler.next_run(every, hour, datetime.now(UTC))
        else:
            anchor = scheduler.as_utc(schedule.last_run_at or datetime.now(UTC))
            schedule.next_run_at = anchor + timedelta(hours=every)
    if payload.initial_window_days is not None:
        schedule.initial_window_days = payload.initial_window_days
    if payload.enabled is not None and payload.enabled != schedule.enabled:
        schedule.enabled = payload.enabled
        if payload.enabled:
            schedule.paused_at = schedule.paused_by = None
        else:
            # Who did it, when sign-in tells us; the audit log has the call either way.
            claims = getattr(request.state, "user", None) or {}
            name = claims.get("preferred_username") or claims.get("name")
            schedule.paused_at = datetime.now(UTC)
            schedule.paused_by = str(name)[:500] if name else None
    session.commit()
    session.refresh(schedule)
    return _read(schedule, _job_statuses(session, [schedule]))


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(
    schedule_id: int, session: Annotated[Session, Depends(get_session)]
) -> Response:
    schedule = _one(session, schedule_id)
    # A default schedule removed on purpose must not come back.
    decline(session, schedule.kind, schedule.target)
    session.delete(schedule)
    session.commit()
    return Response(status_code=204)


@router.post("/schedules/{schedule_id}/run", response_model=ScheduleRead, status_code=202)
def run_schedule_now(
    schedule_id: int,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> ScheduleRead:
    """Start the schedule's job now and restart its cadence from now."""
    now = datetime.now(UTC)
    # The last job's outcome first: a pull continues from what was collected.
    scheduler.settle(session, now)
    schedule = _one(session, schedule_id)
    try:
        job = scheduler.start_job(session, schedule, now, origin="manual")
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=scheduler.BUSY_ERROR) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    schedule.next_run_at = scheduler.next_run_for(schedule, now)
    scheduler.record_attempt(session, schedule, now, job, None)
    background.add_task(scheduler.worker_for(job.kind), job.job_id)
    session.refresh(schedule)
    return _read(schedule, _job_statuses(session, [schedule]))
