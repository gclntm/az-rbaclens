"""Shared lifecycle helpers for background jobs (scans, activity pulls).

Both workers follow the same shape — load the row, mark ``running``,
do network + DB work, mark ``done`` — and both had the same failure
bug: on a flush error the ``except`` branch wrote ``status="failed"``
into a session whose transaction had already failed, so the final
commit raised ``PendingRollbackError`` and the row stayed ``running``
until the sweeper flipped it an hour later.

``mark_job_failed`` is the fix: roll back whatever is pending (so a
failed job never carries a partial result set), then record the
failure with a Core UPDATE in a fresh transaction. It never raises —
if even that write fails, the sweeper is the last line of defence and
the exception is logged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ActivityPull, Scan

logger = logging.getLogger(__name__)


_INFLIGHT = ("pending", "running")


def start_job(session: Session, model: type, job_id: int) -> bool:
    """Move the job from ``pending`` to ``running`` and commit, or return
    ``False`` when it is no longer pending — the sweeper failed it while it
    waited, so the worker must not bring it back to life."""
    now = datetime.now(UTC)
    result = session.execute(
        update(model)
        .where(model.id == job_id)
        .where(model.status == "pending")
        .values(status="running", started_at=now, heartbeat_at=now)
    )
    if not result.rowcount:
        session.rollback()
        logger.warning(
            "%s %s is no longer pending; its worker does not start",
            getattr(model, "__tablename__", model),
            job_id,
        )
        return False
    session.commit()
    return True


def finish_job(session: Session, model: type, job_id: int) -> bool:
    """Commit the job's pending work with ``status="done"``, but only while
    the row is still in flight.

    The stuck-job sweeper may have marked the job ``failed`` while the
    worker was still running (no heartbeat for too long). Its verdict
    stands: a retry may already be under way, and the failure was already
    reported. The guarded UPDATE is a compare-and-set — the database
    serialises it against the sweeper's own UPDATE, so exactly one of the
    two terminal states lands. Returns ``False`` (and rolls back the
    worker's pending results) when the sweeper got there first.
    """
    session.flush()
    result = session.execute(
        update(model)
        .where(model.id == job_id)
        .where(model.status.in_(_INFLIGHT))
        .values(status="done", finished_at=datetime.now(UTC))
    )
    if not result.rowcount:
        session.rollback()
        logger.warning(
            "%s %s was marked failed while its worker ran; the late result is discarded",
            getattr(model, "__tablename__", model),
            job_id,
        )
        return False
    session.commit()
    return True


def mark_job_failed(session: Session, model: type, job_id: int, error: str) -> None:
    """Roll back and record ``error`` on ``model`` row ``job_id`` as ``failed``."""
    try:
        session.rollback()
        session.execute(
            update(model)
            .where(model.id == job_id)
            .values(status="failed", error=error, finished_at=datetime.now(UTC))
        )
        session.commit()
    except Exception:
        logger.exception(
            "could not record failure for %s %s", getattr(model, "__tablename__", model), job_id
        )
        try:
            session.rollback()
        except Exception:
            logger.exception("rollback after failed failure-record also failed")


def create_scan(
    session: Session,
    scope: str,
    scope_level: str,
    schedule_id: int | None = None,
    origin: str = "manual",
) -> Scan:
    """Insert a pending scan and commit, or raise ``IntegrityError`` (rolled
    back) when one for the same scope is already pending/running — the
    partial unique index ``uq_scans_inflight_scope`` is the guard."""
    scan = Scan(
        scope=scope,
        scope_level=scope_level,
        status="pending",
        schedule_id=schedule_id,
        origin=origin,
    )
    session.add(scan)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(scan)
    return scan


def create_pull(
    session: Session,
    subscription_id: str,
    start: datetime,
    end: datetime,
    schedule_id: int | None = None,
    origin: str = "manual",
) -> ActivityPull:
    """Insert a pending activity pull and commit, or raise ``IntegrityError``
    (rolled back) when one for the subscription is already in flight —
    guarded by ``uq_activity_pulls_inflight_subscription``."""
    pull = ActivityPull(
        subscription_id=subscription_id,
        start_time=start,
        end_time=end,
        status="pending",
        schedule_id=schedule_id,
        origin=origin,
    )
    session.add(pull)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise
    session.refresh(pull)
    return pull
