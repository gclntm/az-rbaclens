"""Stuck-job sweeper (scans and activity pulls).

A job's terminal transition (``running`` → ``done`` / ``failed``) is
owned by the worker thread. If that worker dies (OOM, container
restart, an exception that escapes the worker's own handling) the row
stays at ``pending`` / ``running`` forever and the UI shows a spinner
that never resolves.

Staleness is measured from ``heartbeat_at`` — bumped by the worker on
every progress update — falling back to ``started_at`` then
``created_at`` for rows that never got that far. Measuring from
``started_at`` (the previous behaviour) flipped legitimately long scans
to ``failed`` while their worker was still running, and the worker's
later ``done`` then silently overwrote the sweeper's verdict.

This module exposes:

- ``sweep_stuck_scans(db, timeout)`` / ``sweep_stuck_pulls(db, timeout)``
  — the pure DB operations. One UPDATE each, returning rows touched.
- ``run_sweeper_loop(stop_event)`` — the long-running asyncio task that
  ``app.main.lifespan`` schedules. Wakes every
  ``stuck_scan_sweep_seconds`` and runs both sweeps. Survives DB errors
  so a transient outage doesn't kill the loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import ActivityPull, Scan

logger = logging.getLogger(__name__)

_INFLIGHT = ("pending", "running")


def _sweep(db: Session, model: type, timeout_seconds: int, noun: str) -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
    last_sign_of_life = func.coalesce(model.heartbeat_at, model.started_at, model.created_at)
    stmt = (
        update(model)
        .where(model.status.in_(_INFLIGHT))
        .where(last_sign_of_life < cutoff)
        .values(
            status="failed",
            error=(
                f"Marked failed by sweeper: {noun} sent no heartbeat for "
                f"> {timeout_seconds}s. The worker likely crashed or was "
                "restarted mid-run."
            ),
            finished_at=datetime.now(UTC),
        )
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def sweep_stuck_scans(db: Session, timeout_seconds: int) -> int:
    """Mark in-flight scans with no heartbeat for ``timeout_seconds`` as failed."""
    return _sweep(db, Scan, timeout_seconds, "scan")


def sweep_stuck_pulls(db: Session, timeout_seconds: int) -> int:
    """Same as :func:`sweep_stuck_scans` for activity pulls."""
    return _sweep(db, ActivityPull, timeout_seconds, "activity pull")


async def run_sweeper_loop(stop_event: asyncio.Event) -> None:
    """Background task. Sweeps every ``stuck_scan_sweep_seconds`` until stopped.

    Uses ``stop_event.wait()`` with a timeout instead of ``asyncio.sleep``
    so shutdown is immediate — without this, ``docker compose down``
    waits up to a full sweep interval for the loop to exit.
    """
    interval = settings.stuck_scan_sweep_seconds
    timeout = settings.stuck_scan_timeout_seconds
    if interval <= 0:
        logger.info("stuck-job sweeper disabled (stuck_scan_sweep_seconds=%d)", interval)
        return

    logger.info("stuck-job sweeper started: interval=%ds, timeout=%ds", interval, timeout)
    # Lazy import so test fixtures that monkeypatch ``app.db.session.SessionLocal``
    # see their replacement (matches the pattern in core/audit.py).
    from app.db.session import SessionLocal

    def sweep_once() -> tuple[int, int]:
        with SessionLocal() as db:
            return sweep_stuck_scans(db, timeout), sweep_stuck_pulls(db, timeout)

    while not stop_event.is_set():
        try:
            # In a thread: a slow database must not hold up every request.
            scans, pulls = await asyncio.to_thread(sweep_once)
            if scans or pulls:
                logger.warning(
                    "stuck-job sweeper marked %d scans, %d pulls as failed", scans, pulls
                )
        except Exception:
            # A transient DB blip shouldn't kill the loop. Log and try
            # again next tick.
            logger.exception("stuck-job sweeper iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue

    logger.info("stuck-job sweeper stopped")
