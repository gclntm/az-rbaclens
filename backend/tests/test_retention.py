"""Retention: snapshot selection (pure), event / snapshot / audit pruning, admin API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.retention import SnapshotInfo, snapshots_to_prune

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
SUB = "/subscriptions/s1"
RG = f"{SUB}/resourcegroups/rg"


def _snap(id_: int, scope: str, status: str = "done", age_days: float = 0.0) -> SnapshotInfo:
    at = NOW - timedelta(days=age_days)
    return SnapshotInfo(id_, scope, status, at if status != "pending" else None, at)


def test_keeps_newest_n_per_scope_and_every_owner():
    scans = [_snap(i, SUB, age_days=10 - i) for i in range(1, 6)]  # 5 newest, 1 oldest
    scans += [
        _snap(10, RG, age_days=1),
        _snap(11, RG.upper(), age_days=2),
    ]  # same scope, other case
    surplus, expired = snapshots_to_prune(
        scans, owners={1}, now=NOW, keep_per_scope=2, failed_days=7
    )
    # SUB keeps 5 and 4; 3 and 2 go; 1 stays because it still owns a scope.
    assert surplus == {2, 3}
    assert expired == set()  # RG has exactly 2


def test_failed_expire_after_n_days_and_in_flight_never_go():
    scans = [
        _snap(1, SUB, "failed", age_days=8),
        _snap(2, SUB, "failed", age_days=6),
        _snap(3, SUB, "running", age_days=30),
        _snap(4, SUB, "pending", age_days=30),
    ]
    surplus, expired = snapshots_to_prune(
        scans, owners=set(), now=NOW, keep_per_scope=1, failed_days=7
    )
    assert (surplus, expired) == (set(), {1})


def test_zero_disables_each_policy():
    scans = [_snap(i, SUB, age_days=i) for i in range(1, 9)] + [
        _snap(20, SUB, "failed", age_days=99)
    ]
    assert snapshots_to_prune(scans, set(), NOW, keep_per_scope=0, failed_days=0) == (set(), set())


def _seed(db):
    """Three done snapshots of SUB (the newest owns it), one old failed, events, audit."""
    from app.db.models import ActivityEvent, AuditEvent, Scan, Scope

    now = datetime.now(UTC)
    scans = [
        Scan(
            scope=SUB,
            scope_level="subscription",
            status=st,
            created_at=now - timedelta(days=d),
            finished_at=now - timedelta(days=d),
        )
        for st, d in (("done", 30), ("done", 20), ("done", 10), ("failed", 9))
    ]
    db.add_all(scans)
    db.flush()
    db.add(
        Scope(
            id=SUB,
            name="prod",
            level="subscription",
            latest_scan_id=scans[2].id,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    db.add_all(
        ActivityEvent(
            subscription_id="s1",
            event_data_id=f"e{i}",
            operation_name="op",
            occurred_at=now - timedelta(days=d),
        )
        for i, d in enumerate((1, 50, 119, 121, 200))
    )
    db.add_all(
        AuditEvent(
            occurred_at=now - timedelta(days=d),
            actor="a",
            method="POST",
            path="/x",
            status_code=200,
        )
        for d in (1, 400)
    )
    db.commit()
    return [s.id for s in scans]


def test_run_retention_prunes_all_three(app_with_sqlite: TestClient, monkeypatch):
    from sqlalchemy import func, select

    from app.core import retention
    from app.core.config import settings
    from app.db.models import ActivityEvent, AuditEvent, Scan
    from app.db.session import SessionLocal

    monkeypatch.setattr(settings, "snapshot_keep_per_scope", 1)
    monkeypatch.setattr(retention, "_EVENT_BATCH", 1)  # exercise the batch loop
    with SessionLocal() as db:
        ids = _seed(db)

    r = app_with_sqlite.post("/api/settings/database/retention")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["events_deleted"],
        body["snapshots_deleted"],
        body["failed_snapshots_deleted"],
        body["audit_deleted"],
    ) == (2, 2, 1, 1)

    with SessionLocal() as db:
        assert db.scalars(select(Scan.id)).all() == [ids[2]]
        assert db.scalar(select(func.count()).select_from(ActivityEvent)) == 3
        # The seeded 400-day-old row is gone (the POST itself adds a fresh audit row).
        assert db.scalars(select(AuditEvent.path).where(AuditEvent.actor == "a")).all() == ["/x"]

    policy = app_with_sqlite.get("/api/settings/database/retention").json()
    assert policy["event_retention_days"] == 120 and policy["snapshot_keep_per_scope"] == 1


def test_old_pull_records_are_pruned_but_not_the_ones_still_in_use(app_with_sqlite: TestClient):
    from sqlalchemy import select

    from app.core.retention import prune_pulls
    from app.db.models import ActivityPull, JobSchedule
    from app.db.session import SessionLocal

    now = datetime.now(UTC)

    def pull(sub: str, days: int, status: str = "done") -> ActivityPull:
        t = now - timedelta(days=days)
        return ActivityPull(
            subscription_id=sub, start_time=t, end_time=t, status=status, created_at=t
        )

    with SessionLocal() as db:
        rows = {
            "old": pull("sub-a", 60),
            "old-failed": pull("sub-a", 50, "failed"),
            "recent": pull("sub-a", 2),
            "only-one": pull("sub-b", 200),  # newest finished pull of its subscription
            "last-job": pull("sub-c", 90, "failed"),  # a schedule's last job
            "newest-c": pull("sub-c", 80),
            "stuck": pull("sub-d", 100, "running"),  # in flight: the sweeper's business
        }
        db.add_all(rows.values())
        db.flush()
        db.add(
            JobSchedule(
                kind="activity_pull",
                target="sub-c",
                every_hours=1,
                next_run_at=now,
                last_job_id=rows["last-job"].id,
            )
        )
        db.commit()
        ids = {k: r.id for k, r in rows.items()}

        assert prune_pulls(db, now, 0) == 0  # 0 keeps everything
        assert prune_pulls(db, now, 30) == 2
        left = set(db.scalars(select(ActivityPull.id)).all())
    assert left == {ids[k] for k in ("recent", "only-one", "last-job", "newest-c", "stuck")}
