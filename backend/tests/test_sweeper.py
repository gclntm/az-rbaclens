"""Tests for the stuck-scan sweeper.

The sweeper is a pure SQL operation, so we exercise it against the same
in-memory SQLite fixture as the rest of the test suite. We seed a few
scans with carefully chosen ``created_at`` / ``started_at`` values and
assert the right ones get flipped to ``failed``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

_seq = 0


def _seed_scan(
    db,
    *,
    status: str,
    created_at: datetime,
    started_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
):
    from app.db.models import Scan

    # Distinct scopes: the in-flight uniqueness index allows only one
    # pending/running scan per scope.
    global _seq
    _seq += 1
    scan = Scan(
        scope=f"/providers/microsoft.management/managementgroups/mg-test-{_seq}",
        scope_level="mgmt_group",
        status=status,
        created_at=created_at,
        started_at=started_at,
        heartbeat_at=heartbeat_at,
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)
    return scan


def _seed_pull(db, *, status: str, created_at: datetime, heartbeat_at: datetime | None = None):
    from app.db.models import ActivityPull

    global _seq
    _seq += 1
    pull = ActivityPull(
        subscription_id=f"00000000-0000-0000-0000-{_seq:012d}",
        start_time=created_at,
        end_time=created_at,
        status=status,
        created_at=created_at,
        started_at=created_at,
        heartbeat_at=heartbeat_at,
    )
    db.add(pull)
    db.commit()
    db.refresh(pull)
    return pull


def test_sweeper_marks_old_running_scans_failed(app_with_sqlite: TestClient):
    from app.core.sweeper import sweep_stuck_scans
    from app.db.session import SessionLocal

    now = datetime.now(UTC)

    with SessionLocal() as db:
        stale_id = _seed_scan(
            db,
            status="running",
            created_at=now - timedelta(hours=3),
            started_at=now - timedelta(hours=2),
        ).id
        fresh_id = _seed_scan(
            db,
            status="running",
            created_at=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
        ).id
        orphan_id = _seed_scan(
            db,
            status="pending",
            created_at=now - timedelta(hours=4),
            started_at=None,
        ).id
        done_id = _seed_scan(
            db,
            status="done",
            created_at=now - timedelta(days=1),
            started_at=now - timedelta(days=1),
        ).id

        affected = sweep_stuck_scans(db, timeout_seconds=3600)

    assert affected == 2

    with SessionLocal() as db:
        from app.db.models import Scan

        stale = db.get(Scan, stale_id)
        assert stale.status == "failed"
        assert stale.error and "sweeper" in stale.error
        assert stale.finished_at is not None

        assert db.get(Scan, orphan_id).status == "failed"
        assert db.get(Scan, fresh_id).status == "running"
        assert db.get(Scan, done_id).status == "done"


def test_sweeper_noop_when_nothing_stuck(app_with_sqlite: TestClient):
    from app.core.sweeper import sweep_stuck_scans
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        _seed_scan(db, status="running", created_at=now, started_at=now)
        affected = sweep_stuck_scans(db, timeout_seconds=3600)

    assert affected == 0


def test_sweeper_uses_started_at_when_present(app_with_sqlite: TestClient):
    """A scan created long ago but picked up recently is NOT stuck."""
    from app.core.sweeper import sweep_stuck_scans
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        scan_id = _seed_scan(
            db,
            status="running",
            created_at=now - timedelta(days=1),
            started_at=now - timedelta(minutes=2),
        ).id
        affected = sweep_stuck_scans(db, timeout_seconds=3600)

    assert affected == 0
    with SessionLocal() as db:
        from app.db.models import Scan

        assert db.get(Scan, scan_id).status == "running"


def test_sweeper_respects_recent_heartbeat(app_with_sqlite: TestClient):
    """A scan that started hours ago but is still reporting progress is NOT stuck."""
    from app.core.sweeper import sweep_stuck_scans
    from app.db.models import Scan
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        alive_id = _seed_scan(
            db,
            status="running",
            created_at=now - timedelta(hours=5),
            started_at=now - timedelta(hours=4),
            heartbeat_at=now - timedelta(seconds=30),
        ).id
        silent_id = _seed_scan(
            db,
            status="running",
            created_at=now - timedelta(hours=5),
            started_at=now - timedelta(hours=4),
            heartbeat_at=now - timedelta(hours=2),
        ).id
        affected = sweep_stuck_scans(db, timeout_seconds=3600)

    assert affected == 1
    with SessionLocal() as db:
        assert db.get(Scan, alive_id).status == "running"
        assert db.get(Scan, silent_id).status == "failed"
        assert "heartbeat" in db.get(Scan, silent_id).error


def test_sweeper_sweeps_stuck_pulls(app_with_sqlite: TestClient):
    from app.core.sweeper import sweep_stuck_pulls
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        stuck_id = _seed_pull(db, status="running", created_at=now - timedelta(hours=3)).id
        fresh_id = _seed_pull(
            db,
            status="running",
            created_at=now - timedelta(hours=3),
            heartbeat_at=now - timedelta(minutes=1),
        ).id
        done_id = _seed_pull(db, status="done", created_at=now - timedelta(days=2)).id
        affected = sweep_stuck_pulls(db, timeout_seconds=3600)

    assert affected == 1
    with SessionLocal() as db:
        assert db.get(ActivityPull, stuck_id).status == "failed"
        assert db.get(ActivityPull, fresh_id).status == "running"
        assert db.get(ActivityPull, done_id).status == "done"


async def test_run_sweeper_loop_exits_on_stop_event(app_with_sqlite: TestClient):
    """The loop must wake on ``stop_event.set()`` rather than waiting a full interval."""
    import asyncio

    from app.core import sweeper as sweeper_mod
    from app.core.config import settings

    # Run the loop with a long interval so the stop signal is the only
    # way it exits within the test timeout.
    original = settings.stuck_scan_sweep_seconds
    settings.stuck_scan_sweep_seconds = 30
    try:
        stop = asyncio.Event()
        task = asyncio.create_task(sweeper_mod.run_sweeper_loop(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        settings.stuck_scan_sweep_seconds = original
