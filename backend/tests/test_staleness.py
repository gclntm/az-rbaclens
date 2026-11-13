"""Warnings when the data stops being kept up to date (core/staleness.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

ROOT = "/providers/microsoft.management/managementgroups/root-id"
SUB_A = "aaaaaaaa-0000-0000-0000-000000000001"
SUB_B = "bbbbbbbb-0000-0000-0000-000000000002"
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


def _world(
    *,
    check_age_h: float = 2,
    check_status: str = "done",
    pull_lag_h: float = 0.5,
    enabled: bool = True,
    running_pull: bool = False,
    now: datetime = NOW,
) -> None:
    """A daily check of the root and hourly pulls of two subscriptions, as configured."""
    NOW = now
    from app.db.models import ActivityPull, JobSchedule, Scan, Scope
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        for sid, name, level in (
            (ROOT, "Tenant Root Group", "mgmt_group"),
            (f"/subscriptions/{SUB_A}", "prod", "subscription"),
            (f"/subscriptions/{SUB_B}", "dev", "subscription"),
        ):
            db.add(Scope(id=sid, name=name, level=level, first_seen_at=NOW, last_seen_at=NOW))
        done = Scan(
            scope=ROOT,
            scope_level="mgmt_group",
            status="done",
            created_at=NOW - timedelta(hours=check_age_h),
            finished_at=NOW - timedelta(hours=check_age_h),
        )
        db.add(done)
        db.flush()
        last = done
        if check_status == "failed":
            last = Scan(
                scope=ROOT,
                scope_level="mgmt_group",
                status="failed",
                error="AuthorizationFailed\nmore",
                created_at=NOW - timedelta(hours=1),
                finished_at=NOW - timedelta(hours=1),
            )
            db.add(last)
            db.flush()
        db.add(
            JobSchedule(
                kind="snapshot",
                target=ROOT,
                every_hours=24,
                at_hour=6,
                enabled=enabled,
                next_run_at=NOW + timedelta(hours=16),
                last_job_id=last.id,
            )
        )
        for sub in (SUB_A, SUB_B):
            end = NOW - timedelta(hours=pull_lag_h)
            db.add(
                JobSchedule(
                    kind="activity_pull",
                    target=sub,
                    every_hours=1,
                    enabled=enabled,
                    next_run_at=NOW + timedelta(minutes=30),
                    covered_until=end,
                )
            )
            db.add(
                ActivityPull(
                    subscription_id=sub,
                    start_time=end - timedelta(hours=2),
                    end_time=end,
                    status="done",
                )
            )
        if running_pull:
            db.add(
                ActivityPull(
                    subscription_id=SUB_A,
                    start_time=NOW - timedelta(hours=9),
                    end_time=NOW,
                    status="running",
                )
            )
        db.commit()


def _warnings():
    from app.core.staleness import warnings
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return warnings(db, NOW)


def test_nothing_to_say_when_everything_runs(app_with_sqlite: TestClient):
    _world()
    assert _warnings() == []


def test_schedules_switched_off_are_named(app_with_sqlite: TestClient):
    _world(enabled=False)
    found = _warnings()
    assert [w.kind for w in found] == ["schedules_off"]  # data still recent: only the switch-off
    assert found[0].text.startswith(
        "3 schedules are off: the check of Tenant Root Group and the Activity Log of 2"
    )
    assert found[0].names == ["Tenant Root Group", "prod", "dev"]


def test_a_failed_and_overdue_check(app_with_sqlite: TestClient):
    _world(check_age_h=30, check_status="failed")
    kinds = {w.kind: w.text for w in _warnings()}
    assert kinds["check_failed"] == (
        "The last scheduled check failed. The app's identity is not allowed to read this in Azure."
    )
    assert "last ran 30 hours ago" in kinds["check_stale"]


def test_activity_log_behind_unless_a_pull_is_running(app_with_sqlite: TestClient):
    _world(pull_lag_h=5)
    (behind,) = _warnings()
    assert behind.kind == "activity_behind" and "up to 5 hours behind" in behind.text
    assert sorted(behind.names) == ["dev", "prod"]


def test_a_running_pull_is_not_behind(app_with_sqlite: TestClient):
    _world(pull_lag_h=5, running_pull=True)
    (behind,) = _warnings()
    assert behind.names == ["dev"] and behind.text.startswith("The Activity Log of dev ")


def test_long_delays_read_in_days_and_one_schedule_is_it(app_with_sqlite: TestClient):
    from app.db.models import JobSchedule
    from app.db.session import SessionLocal

    _world(pull_lag_h=100)
    with SessionLocal() as db:
        db.get(JobSchedule, 2).enabled = False
        db.commit()
    off, behind = _warnings()
    assert off.text == (
        "A schedule is off: the Activity Log of prod. "
        "Nothing updates it until it's on again; delete it if you don't want it."
    )
    assert behind.text.startswith("The Activity Log of dev is up to 4 days behind")


def test_no_check_scheduled(app_with_sqlite: TestClient):
    assert [w.kind for w in _warnings()] == ["no_check"]


def test_the_admins_get_one_email_a_day(app_with_sqlite: TestClient):
    from sqlalchemy import select

    from app.core.notify_email import compose
    from app.core.staleness import notify_admins
    from app.db.models import Notification
    from app.db.session import SessionLocal

    _world(enabled=False)
    with SessionLocal() as db:
        assert notify_admins(db, NOW) == 1
        assert notify_admins(db, NOW + timedelta(hours=3)) == 0  # same day, same problems
        assert notify_admins(db, NOW + timedelta(days=1)) == 1
        n = db.scalars(select(Notification).where(Notification.kind == "data_stale")).first()
    assert n.recipient_id is None and n.payload["path"] == "/admin/data"
    subject, body = compose(n, "http://rbaclens.local")
    assert subject == "RBAC Lens data is not up to date: 1 problem(s)"
    assert "<li>3 schedules are off" in body and 'href="http://rbaclens.local/admin/data"' in body


def test_freshness_lists_the_warnings(app_with_sqlite: TestClient):
    _world(enabled=False, now=datetime.now(UTC))
    body = app_with_sqlite.get("/api/freshness").json()
    assert [w["kind"] for w in body["warnings"]] == ["schedules_off"]
    assert body["warnings"][0]["path"] == "/admin/data"
