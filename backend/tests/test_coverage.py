"""Activity Log coverage: days left before Azure forgets, the red alarm, the nightly second pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SUBS = {
    "prod": "aaaaaaaa-0000-0000-0000-000000000001",
    "test": "bbbbbbbb-0000-0000-0000-000000000002",
    "old": "cccccccc-0000-0000-0000-000000000003",
    "gone": "dddddddd-0000-0000-0000-000000000004",
    "new": "eeeeeeee-0000-0000-0000-000000000005",
    "orphan": "ffffffff-0000-0000-0000-000000000006",
}


def _seed(now: datetime = NOW) -> None:
    """prod up to date; test 12 days behind and paused; old 70 days behind; gone 95 days;
    new never collected; orphan known to the checks but without a schedule."""
    from app.db.models import JobSchedule, Scope
    from app.db.session import SessionLocal

    behind = {
        "prod": timedelta(minutes=20),
        "test": timedelta(days=12),
        "old": timedelta(days=70),
        "gone": timedelta(days=95),
        "new": None,
    }
    with SessionLocal() as db:
        for name, sub in SUBS.items():
            db.add(
                Scope(
                    id=f"/subscriptions/{sub}",
                    name=name,
                    level="subscription",
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            if name == "orphan":
                continue
            db.add(
                JobSchedule(
                    kind="activity_pull",
                    target=sub,
                    every_hours=1,
                    enabled=name != "test",
                    next_run_at=now + timedelta(hours=1),
                    covered_until=now - behind[name] if behind[name] is not None else None,
                )
            )
        db.commit()


def test_states_and_days_left(app_with_sqlite: TestClient):
    from app.core.coverage import coverage
    from app.db.session import SessionLocal

    _seed()
    with SessionLocal() as db:
        rows = {c.name: c for c in coverage(db, NOW)}
        order = [c.name for c in coverage(db, NOW)]
    assert {n: c.state for n, c in rows.items()} == {
        "prod": "ok",
        "test": "behind",
        "old": "at_risk",
        "gone": "lost",
        "new": "never",
        "orphan": "no_schedule",
    }
    # Azure keeps 90 days and a day is kept as margin: 89 collectable.
    assert (rows["test"].days_left, rows["old"].days_left, rows["gone"].days_left) == (77, 19, -6)
    assert rows["prod"].days_left == 88 and rows["new"].days_left is None
    assert rows["test"].enabled is False and rows["orphan"].schedule_id is None
    assert order[:2] == ["gone", "old"]  # worst first


def test_the_alarm_turns_red_before_azure_forgets_even_when_paused(app_with_sqlite: TestClient):
    from app.core.staleness import warnings
    from app.db.models import JobSchedule
    from app.db.session import SessionLocal

    _seed()
    with SessionLocal() as db:
        # Pause "old" too: Azure's 90 days run whether collection is on or not.
        db.query(JobSchedule).filter_by(target=SUBS["old"]).one().enabled = False
        db.commit()
        found = warnings(db, NOW)
    by_kind = {w.kind: w for w in found}
    assert [w.severity for w in found][:2] == ["danger", "danger"]  # red first
    assert by_kind["activity_at_risk"].names == ["old"]
    assert (
        "70 days behind" in by_kind["activity_at_risk"].text
        and "19 days left" in by_kind["activity_at_risk"].text
    )
    assert by_kind["activity_lost"].names == ["gone"]
    assert "gone from Azure for good" in by_kind["activity_lost"].text
    # "new" is on and was never collected; the paused ones are named by schedules_off.
    assert by_kind["activity_behind"].names == ["new"]
    assert by_kind["schedules_off"].severity == "warn"


def test_coverage_endpoint(app_with_sqlite: TestClient):
    _seed(datetime.now(UTC))
    body = app_with_sqlite.get("/api/activity/coverage").json()
    assert (body["azure_keeps_days"], body["at_risk_days_left"]) == (90, 30)
    rows = {r["name"]: r for r in body["subscriptions"]}
    assert rows["old"]["state"] == "at_risk" and rows["old"]["days_left"] in (
        18,
        19,
    )  # a moment has passed
    assert rows["prod"]["state"] == "ok" and rows["prod"]["second_pass"] is None
    assert body["second_pass_finished_at"] is None and body["second_pass_late_events"] == 0


# ---- the nightly second pass ----------------------------------------------------------------


@pytest.fixture
def second_pass(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "second_pass_hours", 48)
    monkeypatch.setattr(settings, "second_pass_hour", 3)


def _tick(now: datetime, free_slots: int = 4):
    from app.core import scheduler
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return scheduler.tick(db, now, free_slots)


def test_second_pass_rereads_two_days_up_to_where_the_hourly_pulls_look(
    app_with_sqlite: TestClient, second_pass
):
    from app.core.coverage import SECOND_PASS_ORIGIN, coverage, second_pass_summary
    from app.db.models import ActivityPull, JobSchedule
    from app.db.session import SessionLocal

    _seed()
    with SessionLocal() as db:  # "new" has just had its first collection
        db.query(JobSchedule).filter_by(target=SUBS["new"]).one().covered_until = NOW - timedelta(
            minutes=30
        )
        db.commit()
    [job] = _tick(NOW)  # no hourly pull is due: one second pass starts
    with SessionLocal() as db:
        pull = db.get(ActivityPull, job.job_id)
        assert (pull.origin, pull.subscription_id) == (SECOND_PASS_ORIGIN, SUBS["prod"])
        start, end = (t.replace(tzinfo=UTC) for t in (pull.start_time, pull.end_time))
        assert start == NOW - timedelta(hours=48)
        # prod is collected up to 20 min ago; the hourly pulls re-read the hour before that.
        assert end == NOW - timedelta(minutes=20) - timedelta(hours=1)
        # It is not the schedule's own job: the mark and the retry logic ignore it.
        assert db.query(JobSchedule).filter_by(target=SUBS["prod"]).one().last_job_id is None

    # While it runs, the next tick moves on to the next subscription with something to re-read:
    # "test" is paused, "old" and "gone" collected nothing in the last two days.
    [second] = _tick(NOW + timedelta(minutes=1))
    with SessionLocal() as db:
        assert db.get(ActivityPull, second.job_id).subscription_id == SUBS["new"]
        for job_id, late in ((job.job_id, 3), (second.job_id, 0)):
            p = db.get(ActivityPull, job_id)
            p.status, p.events_persisted, p.finished_at = "done", late, NOW + timedelta(minutes=2)
            p.created_at = NOW  # the test's clock, not the database's: "next night" below
        db.commit()

    assert _tick(NOW + timedelta(minutes=3)) == []  # everyone had tonight's pass
    with SessionLocal() as db:
        summary = second_pass_summary(coverage(db, NOW + timedelta(minutes=3)))
    assert (summary.subscriptions, summary.late_events, summary.failed) == (2, 3, 0)

    # The next night it is due again.
    from app.core import scheduler

    with SessionLocal() as db:
        assert scheduler.start_second_pass(db, NOW + timedelta(days=1)) is not None


def test_second_pass_can_be_turned_off(app_with_sqlite: TestClient):
    _seed()
    assert _tick(NOW) == []  # second_pass_hours is 0 in tests unless asked for


def test_second_pass_waits_for_a_free_worker_and_a_free_subscription(
    app_with_sqlite: TestClient, second_pass
):
    from app.db.models import ActivityPull, JobSchedule
    from app.db.session import SessionLocal

    _seed()
    assert _tick(NOW, free_slots=0) == []
    with SessionLocal() as db:
        # An hourly pull of prod is running: its second pass waits, another subscription goes first.
        db.add(
            ActivityPull(
                subscription_id=SUBS["prod"], start_time=NOW, end_time=NOW, status="running"
            )
        )
        db.query(JobSchedule).filter_by(target=SUBS["old"]).delete()
        db.commit()
    assert _tick(NOW) == []  # prod is busy, the others have nothing to re-read
