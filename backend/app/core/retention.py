"""Data retention: old Activity Log events, surplus snapshots, old audit rows,
old records of Activity Log pulls.

Policy lives in ``app.core.config`` (``event_retention_days``,
``snapshot_keep_per_scope``, ``failed_snapshot_retention_days``,
``audit_retention_days``, ``pull_record_retention_days``; ``0`` keeps everything). ``run_retention`` applies
all of it; ``run_retention_loop`` calls it on a timer from
``app.main.lifespan`` and ``POST /settings/database/retention`` on demand.

Snapshots overlap, so "keep the newest N per scope" is not enough: an old
management-group snapshot can still *own* resource groups no newer
snapshot walked (``scopes.latest_scan_id``). Deleting it would empty those
scopes' current view, so a snapshot any scope points to is never pruned.

Deletes run in batches that commit independently, so a long prune never
holds one big lock on ``activity_events``. Postgres reuses the freed space;
returning it to the OS needs ``VACUUM FULL`` (Settings → Database).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.activity_compaction import compact, days_to_compact, prune_markers
from app.core.config import settings
from app.core.retention_settings import RetentionSettings, effective
from app.core.timeutil import as_utc
from app.db.models import (
    AccessChange,
    ActivityEvent,
    ActivityPull,
    AuditEvent,
    DirectoryAuditEvent,
    JobSchedule,
    Scan,
    Scope,
)

logger = logging.getLogger(__name__)

_EVENT_BATCH = 50_000


class SnapshotInfo(NamedTuple):
    id: int
    scope: str
    status: str
    finished_at: datetime | None
    created_at: datetime


@dataclass
class RetentionResult:
    events_deleted: int = 0
    snapshots_deleted: int = 0
    failed_snapshots_deleted: int = 0
    audit_deleted: int = 0
    changes_deleted: int = 0
    pulls_deleted: int = 0
    days_compacted: int = 0
    events_compacted: int = 0
    rows_after_compaction: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def snapshots_to_prune(
    scans: list[SnapshotInfo],
    owners: set[int],
    now: datetime,
    keep_per_scope: int,
    failed_days: int,
    keep_days: int = 0,
) -> tuple[set[int], set[int]]:
    """``(surplus done snapshots, expired failed snapshots)`` to delete.

    A completed check is kept while it is among the newest ``keep_per_scope``
    of its place, or younger than ``keep_days`` (``0``: no time rule):
    whichever keeps more. Pure: the caller loads the rows and does the
    deleting. In-flight snapshots and any snapshot in ``owners`` are always
    kept.
    """
    surplus: set[int] = set()
    if keep_per_scope > 0:
        young_after = now - timedelta(days=keep_days) if keep_days > 0 else None
        by_scope: dict[str, list[SnapshotInfo]] = defaultdict(list)
        for s in scans:
            if s.status == "done":
                by_scope[s.scope.lower()].append(s)
        for rows in by_scope.values():
            rows.sort(key=lambda s: (as_utc(s.finished_at or s.created_at), s.id), reverse=True)
            surplus.update(
                s.id
                for s in rows[keep_per_scope:]
                if s.id not in owners
                and (young_after is None or as_utc(s.finished_at or s.created_at) < young_after)
            )

    expired: set[int] = set()
    if failed_days > 0:
        cutoff = now - timedelta(days=failed_days)
        expired = {
            s.id
            for s in scans
            if s.status == "failed"
            and as_utc(s.finished_at or s.created_at) < cutoff
            and s.id not in owners
        }
    return surplus, expired


def scope_owner_ids(session: Session) -> set[int]:
    """Snapshots that some scope currently treats as its latest."""
    return {
        sid
        for (sid,) in session.execute(
            select(Scope.latest_scan_id).where(Scope.latest_scan_id.is_not(None)).distinct()
        ).all()
    }


def prune_snapshots(
    session: Session, now: datetime, rs: RetentionSettings | None = None
) -> tuple[int, int]:
    rs = rs or effective(session)
    rows = [
        SnapshotInfo(*r)
        for r in session.execute(
            select(Scan.id, Scan.scope, Scan.status, Scan.finished_at, Scan.created_at)
        ).all()
    ]
    surplus, expired = snapshots_to_prune(
        rows,
        scope_owner_ids(session),
        now,
        rs.snapshot_keep_per_scope,
        rs.failed_snapshot_retention_days,
        rs.snapshot_keep_days,
    )
    if surplus | expired:
        # FKs cascade (role assignments, findings, coverage, memberships).
        session.execute(delete(Scan).where(Scan.id.in_(surplus | expired)))
        session.commit()
    return len(surplus), len(expired)


def prune_events(session: Session, now: datetime, days: int, entra_days: int | None = None) -> int:
    """Delete Activity Log events older than ``days`` and Entra ID audit log
    entries older than ``entra_days`` (``None``: the same); ``0`` keeps them."""
    total = 0
    if days > 0:
        total = _prune_activity(session, now - timedelta(days=days))
    entra_days = days if entra_days is None else entra_days
    if entra_days > 0:
        n = session.execute(
            delete(DirectoryAuditEvent).where(
                DirectoryAuditEvent.occurred_at < now - timedelta(days=entra_days)
            )
        ).rowcount
        session.commit()
        total += n or 0
    return total


def _prune_activity(session: Session, cutoff: datetime) -> int:
    total = 0
    while True:
        ids = select(ActivityEvent.id).where(ActivityEvent.occurred_at < cutoff).limit(_EVENT_BATCH)
        n = session.execute(
            delete(ActivityEvent).where(ActivityEvent.id.in_(ids.scalar_subquery()))
        ).rowcount
        session.commit()
        total += n or 0
        if not n or n < _EVENT_BATCH:
            return total


def prune_audit(session: Session, now: datetime, days: int) -> int:
    if days <= 0:
        return 0
    n = session.execute(
        delete(AuditEvent).where(AuditEvent.occurred_at < now - timedelta(days=days))
    ).rowcount
    session.commit()
    return n or 0


def prune_changes(session: Session, now: datetime, days: int) -> int:
    if days <= 0:
        return 0
    n = session.execute(
        delete(AccessChange).where(AccessChange.occurred_at < now - timedelta(days=days))
    ).rowcount
    session.commit()
    return n or 0


def prune_pulls(session: Session, now: datetime, days: int) -> int:
    """Delete the records of finished Activity Log pulls older than ``days``.

    Only the record of the run goes (window, counts, error); the events it
    collected stay. Kept whatever their age: the newest finished pull of
    each subscription (what "collected up to" is read from) and each
    schedule's last job.
    """
    if days <= 0:
        return 0
    n = session.execute(
        delete(ActivityPull)
        .where(*_old_pulls(now, days))
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    return n or 0


def _old_pulls(now: datetime, days: int) -> tuple:
    """Conditions on ``activity_pulls`` rows retention deletes (see :func:`prune_pulls`)."""
    newest = (
        select(func.max(ActivityPull.id))
        .where(ActivityPull.status == "done")
        .group_by(func.lower(ActivityPull.subscription_id))
    )
    last_jobs = select(JobSchedule.last_job_id).where(
        JobSchedule.kind == "activity_pull", JobSchedule.last_job_id.is_not(None)
    )
    return (
        ActivityPull.status.in_(("done", "failed")),
        ActivityPull.created_at < now - timedelta(days=days),
        ActivityPull.id.not_in(newest),
        ActivityPull.id.not_in(last_jobs),
    )


def preview(session: Session, rs: RetentionSettings, now: datetime | None = None) -> dict[str, int]:
    """What a retention run with ``rs`` would delete (and fold), without doing it.
    Same selections as the run itself."""
    now = now or datetime.now(UTC)
    rows = [
        SnapshotInfo(*r)
        for r in session.execute(
            select(Scan.id, Scan.scope, Scan.status, Scan.finished_at, Scan.created_at)
        ).all()
    ]
    surplus, expired = snapshots_to_prune(
        rows,
        scope_owner_ids(session),
        now,
        rs.snapshot_keep_per_scope,
        rs.failed_snapshot_retention_days,
        rs.snapshot_keep_days,
    )

    def older(column, days: int) -> int:
        if days <= 0:
            return 0
        return session.scalar(select(func.count()).where(column < now - timedelta(days=days))) or 0

    return {
        "checks": len(surplus),
        "failed_checks": len(expired),
        "activity_events": older(ActivityEvent.occurred_at, rs.event_retention_days),
        "entra_audit": older(DirectoryAuditEvent.occurred_at, rs.entra_audit_retention_days),
        "changes": older(AccessChange.occurred_at, rs.change_retention_days),
        "audit": older(AuditEvent.occurred_at, rs.audit_retention_days),
        "pulls": (
            session.scalar(
                select(func.count())
                .select_from(ActivityPull)
                .where(*_old_pulls(now, rs.pull_record_retention_days))
            )
            or 0
        )
        if rs.pull_record_retention_days > 0
        else 0,
        "days_to_fold": len(days_to_compact(session, now, rs.compact_after_days)),
    }


def run_retention(
    session: Session, now: datetime | None = None, compact_limit: int | None = None
) -> RetentionResult:
    now = now or datetime.now(UTC)
    # Saved on Settings › Data kept, else the server's (core/retention_settings.py).
    rs = effective(session)
    result = RetentionResult()
    result.events_deleted = prune_events(
        session, now, rs.event_retention_days, rs.entra_audit_retention_days
    )
    if rs.event_retention_days > 0:
        prune_markers(session, (now - timedelta(days=rs.event_retention_days)).date())
    # Machine activity of days old enough becomes daily counts (core/activity_compaction.py).
    # ``compact_limit`` caps one run, for a call from the Settings page.
    folded = compact(session, now, limit=compact_limit, after_days=rs.compact_after_days)
    result.days_compacted = folded.days
    result.events_compacted = folded.events_before
    result.rows_after_compaction = folded.rows_after
    result.snapshots_deleted, result.failed_snapshots_deleted = prune_snapshots(session, now, rs)
    result.audit_deleted = prune_audit(session, now, rs.audit_retention_days)
    result.changes_deleted = prune_changes(session, now, rs.change_retention_days)
    result.pulls_deleted = prune_pulls(session, now, rs.pull_record_retention_days)
    return result


def _run_once() -> RetentionResult:
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        return run_retention(session)


async def run_retention_loop(stop_event: asyncio.Event) -> None:
    """Background task wired in ``app.main.lifespan``."""
    hours = settings.retention_interval_hours
    if hours <= 0:
        logger.info("retention loop disabled (retention_interval_hours=%d)", hours)
        return
    wait = max(60, settings.retention_first_run_minutes * 60)
    logger.info("retention loop started: first run in %ds, then every %dh", wait, hours)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait)
            break
        except TimeoutError:
            pass
        try:
            result = await asyncio.to_thread(_run_once)
            logger.info("retention applied: %s", result.as_dict())
        except Exception:
            logger.exception("retention run failed")
        wait = hours * 3600
    logger.info("retention loop stopped")
