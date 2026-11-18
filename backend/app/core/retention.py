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

from app.core.activity_compaction import compact, prune_markers
from app.core.config import settings
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
) -> tuple[set[int], set[int]]:
    """``(surplus done snapshots, expired failed snapshots)`` to delete.

    Pure: the caller loads the rows and does the deleting. In-flight
    snapshots and any snapshot in ``owners`` are always kept.
    """
    surplus: set[int] = set()
    if keep_per_scope > 0:
        by_scope: dict[str, list[SnapshotInfo]] = defaultdict(list)
        for s in scans:
            if s.status == "done":
                by_scope[s.scope.lower()].append(s)
        for rows in by_scope.values():
            rows.sort(key=lambda s: (as_utc(s.finished_at or s.created_at), s.id), reverse=True)
            surplus.update(s.id for s in rows[keep_per_scope:] if s.id not in owners)

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


def prune_snapshots(session: Session, now: datetime) -> tuple[int, int]:
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
        settings.snapshot_keep_per_scope,
        settings.failed_snapshot_retention_days,
    )
    if surplus | expired:
        # FKs cascade (role assignments, findings, coverage, memberships).
        session.execute(delete(Scan).where(Scan.id.in_(surplus | expired)))
        session.commit()
    return len(surplus), len(expired)


def prune_events(session: Session, now: datetime, days: int) -> int:
    if days <= 0:
        return 0
    cutoff = now - timedelta(days=days)
    total = 0
    while True:
        ids = select(ActivityEvent.id).where(ActivityEvent.occurred_at < cutoff).limit(_EVENT_BATCH)
        n = session.execute(
            delete(ActivityEvent).where(ActivityEvent.id.in_(ids.scalar_subquery()))
        ).rowcount
        session.commit()
        total += n or 0
        if not n or n < _EVENT_BATCH:
            break
    # The Entra ID audit log's membership changes are kept as long (tools/entra_audit.py).
    n = session.execute(
        delete(DirectoryAuditEvent).where(DirectoryAuditEvent.occurred_at < cutoff)
    ).rowcount
    session.commit()
    return total + (n or 0)


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
    newest = (
        select(func.max(ActivityPull.id))
        .where(ActivityPull.status == "done")
        .group_by(func.lower(ActivityPull.subscription_id))
    )
    last_jobs = select(JobSchedule.last_job_id).where(
        JobSchedule.kind == "activity_pull", JobSchedule.last_job_id.is_not(None)
    )
    n = session.execute(
        delete(ActivityPull)
        .where(
            ActivityPull.status.in_(("done", "failed")),
            ActivityPull.created_at < now - timedelta(days=days),
            ActivityPull.id.not_in(newest),
            ActivityPull.id.not_in(last_jobs),
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    session.commit()
    return n or 0


def run_retention(
    session: Session, now: datetime | None = None, compact_limit: int | None = None
) -> RetentionResult:
    now = now or datetime.now(UTC)
    result = RetentionResult()
    result.events_deleted = prune_events(session, now, settings.event_retention_days)
    if settings.event_retention_days > 0:
        prune_markers(session, (now - timedelta(days=settings.event_retention_days)).date())
    # Machine activity of days old enough becomes daily counts (core/activity_compaction.py).
    # ``compact_limit`` caps one run, for a call from the Settings page.
    folded = compact(session, now, limit=compact_limit)
    result.days_compacted = folded.days
    result.events_compacted = folded.events_before
    result.rows_after_compaction = folded.rows_after
    result.snapshots_deleted, result.failed_snapshots_deleted = prune_snapshots(session, now)
    result.audit_deleted = prune_audit(session, now, settings.audit_retention_days)
    result.changes_deleted = prune_changes(session, now, settings.change_retention_days)
    result.pulls_deleted = prune_pulls(session, now, settings.pull_record_retention_days)
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
