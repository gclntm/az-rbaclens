"""A smaller Activity Log: machine activity as daily counts.

Most of what Azure logs is apps doing the same thing over and over (Azure
Arc reporting its databases every few minutes, backup jobs listing storage
keys). Stored one row per event, with Azure's full record, that made the
table gigabytes. Once a day is old enough not to be read again by a
collection (:data:`COMPACT_AFTER_DAYS`), :func:`compact_day` folds it:

- **kept in full** (:func:`keep_in_full`): everything done by a person, every
  change to access or governance (``Microsoft.Authorization`` and
  ``Microsoft.Management`` writes and deletes, elevated access), and every
  failure;
- **one row per day** for the rest: the same caller doing the same operation
  on the same resource with the same outcome, with how many times
  (``event_count``) and the first and last time; without Azure's record;
- **dropped**: "Started" rows whose outcome is stored (same correlation id
  and operation).

Readers count with ``sum(event_count)``. A compacted day is recorded in
``activity_compacted_days``; a later collection over that day skips what
was folded (:func:`skip_on_compacted_day`), since the events' ids are gone
and it would count them twice.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import ActivityCompactedDay, ActivityEvent

logger = logging.getLogger(__name__)

# Collections re-read at most the last two days (the nightly second pass); a
# day is folded once it is this old, so no scheduled collection reads it again.
COMPACT_AFTER_DAYS = 4
OUTCOMES = ("Succeeded", "Failed", "Canceled", "Cancelled")
DAILY_PREFIX = "daily:"
_CHANGE_PROVIDERS = ("microsoft.authorization/", "microsoft.management/")
_CHUNK = 2000


def is_person(caller: str | None, caller_type: str | None) -> bool:
    return caller_type == "User" or "@" in (caller or "")


def keep_in_full(
    caller: str | None, caller_type: str | None, operation: str | None, status: str | None
) -> bool:
    """Whether an event keeps its own row and Azure's record (see the module doc)."""
    if status == "Failed" or is_person(caller, caller_type):
        return True
    op = (operation or "").lower()
    if op.startswith(_CHANGE_PROVIDERS) and op.endswith(("/write", "/delete")):
        return True
    return op == "microsoft.authorization/elevateaccess/action"


def skip_on_compacted_day(event: ActivityEvent) -> bool:
    """On a compacted day, what a collection must not store again: what was folded
    into a daily row, and "Started" rows (dropped when their outcome is stored)."""
    return event.status == "Started" or not keep_in_full(
        event.caller, event.caller_type, event.operation_name, event.status
    )


def compacted_days(session: Session, target: str, since: datetime) -> set[date]:
    return set(
        session.scalars(
            select(ActivityCompactedDay.day).where(
                ActivityCompactedDay.subscription_id == target,
                ActivityCompactedDay.day >= since.astimezone(UTC).date(),
            )
        ).all()
    )


def utc_day(at: datetime) -> date:
    return as_utc(at).date()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _daily_id(target: str, day: date, key: tuple) -> str:
    digest = hashlib.sha256(repr((target, day.isoformat(), key)).encode()).hexdigest()[:40]
    return f"{DAILY_PREFIX}{digest}"


def compact_day(session: Session, target: str, day: date, now: datetime) -> tuple[int, int]:
    """Fold one day of one target; returns ``(events before, rows after)``. Commits."""
    start, end = _day_bounds(day)
    cols = (
        ActivityEvent.id,
        ActivityEvent.caller,
        ActivityEvent.caller_type,
        ActivityEvent.caller_oid,
        ActivityEvent.operation_name,
        ActivityEvent.resource_id,
        ActivityEvent.resource_type,
        ActivityEvent.status,
        ActivityEvent.correlation_id,
        ActivityEvent.occurred_at,
        ActivityEvent.event_count,
        ActivityEvent.first_occurred_at,
        ActivityEvent.event_data_id,
    )
    rows = session.execute(
        select(*cols).where(
            ActivityEvent.subscription_id == target,
            ActivityEvent.occurred_at >= start,
            ActivityEvent.occurred_at < end,
        )
    ).all()
    before = sum(r.event_count for r in rows)
    # Outcomes can land just after midnight: look a day further for them.
    outcome_keys = set(
        session.execute(
            select(ActivityEvent.correlation_id, ActivityEvent.operation_name).where(
                ActivityEvent.subscription_id == target,
                ActivityEvent.occurred_at >= start,
                ActivityEvent.occurred_at < end + timedelta(days=1),
                ActivityEvent.status.in_(OUTCOMES),
                ActivityEvent.correlation_id.is_not(None),
            )
        ).all()
    )
    drop: list[int] = []
    groups: dict[tuple, list] = {}
    existing_daily: dict[str, int] = {}
    for r in rows:
        if r.event_data_id.startswith(DAILY_PREFIX):
            existing_daily[r.event_data_id] = r.id
            continue
        if r.status == "Started" and (r.correlation_id, r.operation_name) in outcome_keys:
            drop.append(r.id)
            continue
        if keep_in_full(r.caller, r.caller_type, r.operation_name, r.status):
            continue
        key = (
            r.caller,
            r.caller_type,
            r.caller_oid,
            r.operation_name,
            (r.resource_id or "").lower(),
            r.resource_type,
            r.status,
        )
        groups.setdefault(key, []).append(r)
        drop.append(r.id)

    for key, members in groups.items():
        times = [as_utc(m.occurred_at) for m in members]
        count = sum(m.event_count for m in members)
        daily_id = _daily_id(target, day, key)
        if daily_id in existing_daily:  # folded before (a day compacted twice): add to it
            row = session.get(ActivityEvent, existing_daily[daily_id])
            row.event_count += count
            row.first_occurred_at = min(as_utc(row.first_occurred_at or row.occurred_at), *times)
            row.occurred_at = max(as_utc(row.occurred_at), *times)
            continue
        first = members[0]
        session.add(
            ActivityEvent(
                subscription_id=target,
                event_data_id=daily_id,
                caller=first.caller,
                caller_type=first.caller_type,
                caller_oid=first.caller_oid,
                operation_name=first.operation_name,
                resource_id=first.resource_id,
                resource_type=first.resource_type,
                status=first.status,
                correlation_id=None,
                occurred_at=max(times),
                first_occurred_at=min(times),
                event_count=count,
                raw=None,
            )
        )
    for i in range(0, len(drop), _CHUNK):
        session.execute(delete(ActivityEvent).where(ActivityEvent.id.in_(drop[i : i + _CHUNK])))
    after = (
        len(rows)
        - len(drop)
        + sum(1 for k in groups if _daily_id(target, day, k) not in existing_daily)
    )
    marker = session.get(ActivityCompactedDay, (target, day))
    if marker is None:
        session.add(
            ActivityCompactedDay(
                subscription_id=target,
                day=day,
                compacted_at=now,
                events_before=before,
                rows_after=after,
            )
        )
    else:
        marker.compacted_at, marker.rows_after = now, after
    session.commit()
    return before, after


@dataclass
class CompactionResult:
    days: int = 0
    events_before: int = 0
    rows_after: int = 0


def days_to_compact(session: Session, now: datetime) -> list[tuple[str, date]]:
    """``(target, day)`` with stored events, old enough, not compacted yet; oldest first."""
    cutoff = datetime.combine(
        now.astimezone(UTC).date() - timedelta(days=COMPACT_AFTER_DAYS), time.min, tzinfo=UTC
    )
    # The UTC day, whatever the database session's time zone.
    at = ActivityEvent.occurred_at
    day_expr = func.date(
        func.timezone("UTC", at) if session.get_bind().dialect.name == "postgresql" else at
    )
    found = session.execute(
        select(ActivityEvent.subscription_id, day_expr)
        .where(ActivityEvent.occurred_at < cutoff)
        .group_by(ActivityEvent.subscription_id, day_expr)
    ).all()
    done = set(
        session.execute(
            select(ActivityCompactedDay.subscription_id, ActivityCompactedDay.day)
        ).all()
    )
    todo = []
    for target, day in found:
        day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
        if (target, day) not in done:
            todo.append((target, day))
    return sorted(todo, key=lambda t: (t[1], t[0]))


def compact(
    session: Session, now: datetime | None = None, limit: int | None = None
) -> CompactionResult:
    """Fold every day old enough and not folded yet (at most ``limit`` days)."""
    now = now or datetime.now(UTC)
    result = CompactionResult()
    todo = days_to_compact(session, now)
    for target, day in todo[:limit] if limit else todo:
        try:
            before, after = compact_day(session, target, day, now)
        except Exception:
            logger.exception("activity compaction failed for %s on %s", target, day)
            session.rollback()
            continue
        result.days += 1
        result.events_before += before
        result.rows_after += after
        if result.days % 50 == 0:
            logger.info(
                "activity compaction: %d of %d days, %d events into %d rows so far",
                result.days,
                len(todo),
                result.events_before,
                result.rows_after,
            )
    if result.days:
        logger.info(
            "activity compaction: %d days, %d events into %d rows",
            result.days,
            result.events_before,
            result.rows_after,
        )
    return result


def prune_markers(session: Session, before: date) -> int:
    n = session.execute(
        delete(ActivityCompactedDay).where(ActivityCompactedDay.day < before)
    ).rowcount
    session.commit()
    return n or 0
