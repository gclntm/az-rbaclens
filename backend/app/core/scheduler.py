"""Recurring jobs: scheduled snapshots and Activity Log pulls.

A ``job_schedules`` row says "snapshot this scope every N hours" or
"pull this subscription's Activity Log every N hours". The loop here
wakes every ``scheduler_tick_seconds``, claims the schedules that are
due, and starts ordinary ``scans`` / ``activity_pulls`` rows — the same
rows (and workers) the manual endpoints create, so a scheduled job is
indistinguishable from a manual one once it is running.

Design points:

- **Claim, don't lock.** A due schedule is claimed with a conditional
  UPDATE that advances ``next_run_at`` only if it still holds the value
  we read. Two replicas (or a tick racing "Run now") can't both start
  the same schedule, and no row lock is held across the job insert.
- **Bounded concurrency.** Workers run on a small thread pool and a
  tick claims at most as many schedules as there are free slots. The
  rest stay due and go on the next tick, rather than queueing as
  ``pending`` rows the stuck-job sweeper would eventually fail.
- **In-flight guard.** The partial unique indexes that 409 the manual
  endpoints also stop a schedule from stacking a second job on a slow
  one; that attempt is recorded in ``last_error`` and retried shortly.
- **Incremental pulls.** A pull starts an hour before the end of the
  last window *this schedule* collected (``covered_until``; late-arriving
  events, inserts dedupe on ``event_data_id``), capped a day inside
  Azure's 90-day retention. The first run looks back
  ``initial_window_days``. Manual pulls never move the mark, so a short
  one after an outage cannot hide the gap.
- **Outcomes are settled on the schedule.** Each tick looks at the last
  job of every schedule (:func:`settle`): a success clears the error and
  the failure count; a failure keeps its error on the schedule and brings
  the next run forward (:data:`RETRY_AFTER`), so a daily check that hit a
  network cut tries again in minutes, not tomorrow.
- **A restart interrupts, it doesn't fail.** At start, jobs the previous
  process left in flight are closed as interrupted and their schedules
  run again on the first tick (:func:`recover_interrupted`).
- **A second pass every night.** From ``second_pass_hour`` each
  subscription's last ``second_pass_hours`` are read once more, up to
  where the hourly pulls will still look, so an event Azure delivered late
  is not lost (:func:`start_second_pass`). One per tick, when a worker is
  free; what it inserts shows on the page as "late events".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.activity_targets import MGMT_GROUPS, is_mgmt_groups
from app.core.config import settings
from app.core.coverage import SECOND_PASS_ORIGIN
from app.core.jobs import create_pull, create_scan
from app.core.timeutil import as_utc
from app.db.models import ActivityPull, JobSchedule, Scan
from app.tools.scope_ids import looks_like_guid, parse_scope_input

logger = logging.getLogger(__name__)

KINDS = ("snapshot", "activity_pull")
RETENTION_DAYS = 90
# Azure checks the start against its retention on every page request,
# not just the first, and a 90-day pull pages for hours: a start of
# exactly now − 90 days goes out of range mid-pull and the pull fails
# ("The start time cannot be more than 90 days in the past").
RETENTION_MARGIN = timedelta(days=1)
PULL_OVERLAP = timedelta(hours=1)
# How soon to retry a schedule whose previous job is still in flight.
BUSY_RETRY = timedelta(minutes=5)
BUSY_ERROR = "Skipped: the previous job for this target is still pending or running."
# How soon a schedule tries again after its 1st, 2nd, 3rd… failure in a row,
# when that is sooner than its normal next run.
RETRY_AFTER = (timedelta(minutes=15), timedelta(hours=1), timedelta(hours=4))
INTERRUPTED_ERROR = (
    "Interrupted: the app restarted while this was running. It runs again by itself."
)
_INFLIGHT = ("pending", "running")
_ERROR_MAX = 1000
# Rows created before this process started cannot have a live worker in it.
PROCESS_STARTED = datetime.now(UTC)


@dataclass(frozen=True)
class StartedJob:
    kind: str  # snapshot | activity_pull
    job_id: int


def normalize_target(kind: str, raw: str) -> str:
    """Validate a schedule target and return its stored form.

    Snapshot targets accept everything the scan form does and are stored
    as the normalised ARM path; pull targets must be a subscription GUID
    (stored lowercased) or ``management-groups`` (core/activity_targets.py).
    Raises ``ValueError`` on bad input.
    """
    if kind == "snapshot":
        scope, _level = parse_scope_input(raw)
        return scope
    if kind == "activity_pull":
        value = raw.strip()
        if is_mgmt_groups(value):
            return MGMT_GROUPS
        if not looks_like_guid(value):
            raise ValueError(
                "activity pull target must be a subscription GUID, or management-groups"
            )
        return value.lower()
    raise ValueError(f"unknown schedule kind {kind!r}")


def retention_floor(now: datetime) -> datetime:
    """The earliest start any pull may ask for: a day inside Azure's retention."""
    return now - timedelta(days=RETENTION_DAYS) + RETENTION_MARGIN


def pull_window(
    covered_until: datetime | None, initial_window_days: int, now: datetime
) -> tuple[datetime, datetime]:
    """The ``(start, end)`` window for a schedule's next pull.

    ``covered_until`` is the end of the newest window the schedule itself
    collected (``None`` before its first success).
    """
    floor = retention_floor(now)
    if covered_until is not None:
        start = as_utc(covered_until) - PULL_OVERLAP
    else:
        start = now - timedelta(days=initial_window_days)
    return max(start, floor), now


def next_run(every_hours: int, at_hour: int | None, now: datetime) -> datetime:
    """When a schedule runs next after ``now`` (UTC).

    Without ``at_hour``: one interval from now. With it (daily schedules):
    the next ``at_hour``:00 in container local time — the ``TZ`` of the
    process, UTC when unset — computed through ``mktime`` so daylight
    saving changes keep the wall-clock hour.
    """
    if at_hour is None:
        return now + timedelta(hours=every_hours)
    local_now = datetime.fromtimestamp(as_utc(now).timestamp())
    candidate = local_now.replace(hour=at_hour, minute=0, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return datetime.fromtimestamp(time.mktime(candidate.timetuple()), UTC)


def next_run_for(schedule: JobSchedule, now: datetime) -> datetime:
    return next_run(schedule.every_hours, schedule.at_hour, now)


def start_job(
    session: Session, schedule: JobSchedule, now: datetime, origin: str = "schedule"
) -> StartedJob:
    """Insert the job row for ``schedule``. Raises ``IntegrityError`` when
    one for the same target is already in flight, ``ValueError`` when the
    stored target no longer parses. ``origin`` is ``manual`` for "Run now"."""
    if schedule.kind == "snapshot":
        scope, level = parse_scope_input(schedule.target)
        return StartedJob("snapshot", create_scan(session, scope, level, schedule.id, origin).id)
    start, end = pull_window(schedule.covered_until, schedule.initial_window_days, now)
    return StartedJob(
        "activity_pull", create_pull(session, schedule.target, start, end, schedule.id, origin).id
    )


def record_attempt(
    session: Session,
    schedule: JobSchedule,
    now: datetime,
    job: StartedJob | None,
    error: str | None,
) -> None:
    """Stamp the outcome of an attempt on the schedule and commit."""
    if job is not None:
        schedule.last_run_at = now
        schedule.last_job_id = job.job_id
        if not schedule.failures or schedule.last_error == BUSY_ERROR:
            # A "could not start" note is over; a failed run's error stays
            # until a run succeeds (see settle).
            schedule.last_error = None
    else:
        schedule.last_error = error
        if error == BUSY_ERROR:
            schedule.next_run_at = now + BUSY_RETRY
    session.commit()


def try_start(session: Session, schedule: JobSchedule, now: datetime) -> StartedJob | None:
    """Start ``schedule``'s job and record the outcome; ``None`` if skipped."""
    try:
        job = start_job(session, schedule, now)
    except IntegrityError:
        record_attempt(session, schedule, now, None, BUSY_ERROR)
        return None
    except ValueError as exc:
        record_attempt(session, schedule, now, None, str(exc))
        return None
    record_attempt(session, schedule, now, job, None)
    return job


def _outcome(
    session: Session, schedule: JobSchedule
) -> tuple[str, str | None, datetime | None] | None:
    """``(status, error, window end)`` of the schedule's last job; ``None`` if the row is gone."""
    if schedule.kind == "snapshot":
        row = session.execute(
            select(Scan.status, Scan.error).where(Scan.id == schedule.last_job_id)
        ).one_or_none()
        return None if row is None else (row[0], row[1], None)
    row = session.execute(
        select(ActivityPull.status, ActivityPull.error, ActivityPull.end_time).where(
            ActivityPull.id == schedule.last_job_id
        )
    ).one_or_none()
    return None if row is None else (row[0], row[1], row[2])


def settle(session: Session, now: datetime) -> int:
    """Record on each schedule how its last job ended. Commits; returns how many were settled.

    - done: no error, no failures; a pull moves ``covered_until`` to its window's end.
    - failed: the error stays on the schedule, and the next run comes after
      :data:`RETRY_AFTER` if that is sooner than planned.
    - interrupted by a restart: not a failure; the schedule is due at once.

    Jobs still in flight are left for a later call.
    """
    pending = session.scalars(
        select(JobSchedule).where(
            JobSchedule.last_job_id.is_not(None),
            or_(
                JobSchedule.settled_job_id.is_(None),
                JobSchedule.settled_job_id != JobSchedule.last_job_id,
            ),
        )
    ).all()
    settled = 0
    for schedule in pending:
        outcome = _outcome(session, schedule)
        if outcome is not None and outcome[0] in _INFLIGHT:
            continue
        if outcome is None:
            pass  # the job row was deleted: nothing to learn from it
        elif outcome[0] == "done":
            schedule.failures = 0
            schedule.last_error = None
            end = outcome[2]
            if end is not None and (
                schedule.covered_until is None or as_utc(end) > as_utc(schedule.covered_until)
            ):
                schedule.covered_until = end
        elif outcome[1] == INTERRUPTED_ERROR:
            if schedule.enabled:
                schedule.next_run_at = now
        else:
            schedule.failures += 1
            schedule.last_error = (outcome[1] or "The job failed without saying why.")[:_ERROR_MAX]
            retry_at = now + RETRY_AFTER[min(schedule.failures, len(RETRY_AFTER)) - 1]
            if schedule.enabled and retry_at < as_utc(schedule.next_run_at):
                schedule.next_run_at = retry_at
        schedule.settled_job_id = schedule.last_job_id
        settled += 1
    session.commit()
    return settled


def recover_interrupted(session: Session, started_before: datetime) -> int:
    """Close scans and pulls a previous process left in flight. Commits.

    Assumes one backend process (the stack's design): anything created
    before this one started has no worker left. The next :func:`settle`
    makes their schedules due again. Returns how many rows were closed.
    """
    closed = 0
    now = datetime.now(UTC)
    for model in (Scan, ActivityPull):
        result = session.execute(
            update(model)
            .where(model.status.in_(_INFLIGHT), model.created_at < started_before)
            .values(status="failed", error=INTERRUPTED_ERROR, finished_at=now)
            .execution_options(synchronize_session=False)
        )
        closed += result.rowcount or 0
    session.commit()
    return closed


def second_pass_boundary(now: datetime) -> datetime:
    """The most recent ``second_pass_hour``:00 (container local time) at or before ``now``."""
    return next_run(24, settings.second_pass_hour, now) - timedelta(days=1)


def start_second_pass(session: Session, now: datetime) -> StartedJob | None:
    """Start the nightly second pass of one subscription that has not had it yet.

    The window ends where the hourly pulls will still look
    (``covered_until`` minus their overlap) and starts
    ``second_pass_hours`` back. Returns ``None`` when every subscription
    is done for tonight, busy, or has too little collected to re-read.
    """
    if settings.second_pass_hours <= 0:
        return None
    boundary = second_pass_boundary(now)
    last = {
        sub: as_utc(at)
        for sub, at in session.execute(
            select(func.lower(ActivityPull.subscription_id), func.max(ActivityPull.created_at))
            .where(ActivityPull.origin == SECOND_PASS_ORIGIN)
            .group_by(func.lower(ActivityPull.subscription_id))
        ).all()
    }
    schedules = session.scalars(
        select(JobSchedule)
        .where(
            JobSchedule.kind == "activity_pull",
            JobSchedule.enabled.is_(True),
            JobSchedule.covered_until.is_not(None),
        )
        .order_by(JobSchedule.id)
    ).all()
    start = max(now - timedelta(hours=settings.second_pass_hours), retention_floor(now))
    for schedule in schedules:
        done_at = last.get(schedule.target.lower())
        if done_at is not None and done_at >= boundary:
            continue
        end = as_utc(schedule.covered_until) - PULL_OVERLAP
        if end <= start:
            continue
        try:
            pull = create_pull(
                session, schedule.target, start, end, schedule.id, SECOND_PASS_ORIGIN
            )
        except IntegrityError:
            continue  # a pull of this subscription is running: next tick
        return StartedJob("activity_pull", pull.id)
    return None


def tick(session: Session, now: datetime, free_slots: int) -> list[StartedJob]:
    """Settle finished jobs, then claim up to ``free_slots`` due schedules and start their jobs;
    with a slot to spare, start one second pass.

    Returns the started jobs; the caller hands them to a worker.
    """
    settle(session, now)
    if free_slots <= 0:
        return []
    due = session.scalars(
        select(JobSchedule)
        .where(JobSchedule.enabled.is_(True), JobSchedule.next_run_at <= now)
        .order_by(JobSchedule.next_run_at, JobSchedule.id)
        .limit(free_slots)
    ).all()
    started: list[StartedJob] = []
    for schedule in due:
        claimed = session.execute(
            update(JobSchedule)
            .where(
                JobSchedule.id == schedule.id,
                JobSchedule.next_run_at == schedule.next_run_at,
            )
            .values(next_run_at=next_run_for(schedule, now))
            .execution_options(synchronize_session=False)
        )
        session.commit()
        if claimed.rowcount != 1:
            continue  # another tick or a "Run now" got there first
        session.refresh(schedule)
        job = try_start(session, schedule, now)
        if job is not None:
            started.append(job)
    if len(started) < free_slots:
        again = start_second_pass(session, now)
        if again is not None:
            started.append(again)
    return started


def worker_for(kind: str) -> Callable[[int], None]:
    # Imported lazily so tests that monkeypatch the worker modules (and
    # ``SessionLocal``) see their replacements.
    if kind == "snapshot":
        from app.tools.iam_access import run_scan

        return run_scan
    from app.tools.activity import run_pull

    return run_pull


def _startup() -> None:
    """Once, before the first tick: create the default schedules and fill
    the changes feed from the history on file if it is still empty."""
    from app.core.changes import rebuild_changes
    from app.core.default_schedules import ensure_default_schedules
    from app.db.models import AccessChange
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        if settings.recover_jobs_at_start:
            try:
                closed = recover_interrupted(session, PROCESS_STARTED)
                if closed:
                    logger.warning(
                        "closed %d job(s) interrupted by the restart; they run again", closed
                    )
            except Exception:
                logger.exception("interrupted jobs could not be recovered")
                session.rollback()
        try:
            ensure_default_schedules(session, datetime.now(UTC))
        except Exception:
            logger.exception("default schedules could not be created")
            session.rollback()
        try:
            from app.core.reviews import open_and_sync

            open_and_sync(session)
        except Exception:
            logger.exception("monthly reviews could not be opened")
            session.rollback()
        try:
            if session.scalar(select(func.count()).select_from(AccessChange)) == 0:
                n = rebuild_changes(session)
                logger.info("changes feed filled from history: %d changes", n)
        except Exception:
            logger.exception("changes feed backfill failed")
            session.rollback()


def _tick_once(executor: ThreadPoolExecutor, running: set[Future]) -> None:
    from app.db.session import SessionLocal

    running.difference_update({f for f in running if f.done()})
    with SessionLocal() as session:
        jobs = tick(
            session,
            datetime.now(UTC),
            settings.scheduler_max_concurrent_jobs - len(running),
        )
    for job in jobs:
        logger.info("scheduler started %s %d", job.kind, job.job_id)
        running.add(executor.submit(worker_for(job.kind), job.job_id))
    try:
        from app.core.findings import flush_recompute

        with SessionLocal() as session:
            recomputed = flush_recompute(session)
        if recomputed:
            logger.info("findings recomputed after new activity: checks %s", recomputed)
    except Exception:
        logger.exception("findings recompute after new activity failed")
    try:
        from app.core.staleness import notify_admins

        with SessionLocal() as session:
            notify_admins(session)
    except Exception:
        logger.exception("stale-data check failed")
    try:
        from app.core.notify import deliver_now

        sent = deliver_now()
        if sent:
            logger.info("notifications sent: %d", sent)
    except Exception:
        logger.exception("notification delivery failed")


async def run_scheduler_loop(stop_event: asyncio.Event) -> None:
    """Background task wired in ``app.main.lifespan``.

    Waits one interval before the first tick so a restart doesn't
    immediately fire everything that came due while it was down.
    ``scheduler_tick_seconds = 0`` disables the loop.
    """
    interval = settings.scheduler_tick_seconds
    if interval <= 0:
        logger.info("job scheduler disabled (scheduler_tick_seconds=%d)", interval)
        return
    workers = max(1, settings.scheduler_max_concurrent_jobs)
    logger.info("job scheduler started: tick=%ds, workers=%d", interval, workers)
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scheduled-job")
    running: set[Future] = set()
    try:
        await asyncio.to_thread(_startup)
    except Exception:
        logger.exception("scheduler startup tasks failed")
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
                break
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(_tick_once, executor, running)
            except Exception:
                logger.exception("job scheduler tick failed")
    finally:
        # Running workers are abandoned at shutdown; the next start closes
        # their rows as interrupted (recover_interrupted), or the stuck-job
        # sweeper does once their heartbeat goes stale.
        executor.shutdown(wait=False, cancel_futures=True)
        logger.info("job scheduler stopped")
