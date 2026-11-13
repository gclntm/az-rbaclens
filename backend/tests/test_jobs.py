"""Scheduled jobs: schedule CRUD, the scheduler tick, run-now, merged jobs list.

``scheduler.tick`` is called directly with an explicit ``now`` — it only
inserts job rows and returns them, so no worker thread is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

MG = "mg-sched"
MG_SCOPE = f"/providers/microsoft.management/managementgroups/{MG}"
SUB = "8A3F1234-1111-2222-3333-AABBCCDDEEFF"
SUB_LOWER = SUB.lower()


def _tick(now: datetime, free_slots: int = 10):
    from app.core import scheduler
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return scheduler.tick(db, now, free_slots)


def _schedule(schedule_id: int):
    from app.db.models import JobSchedule
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        s = db.get(JobSchedule, schedule_id)
        db.expunge(s)
        return s


def _set_job_status(model_name: str, job_id: int, status: str, **extra) -> None:
    from app.db import models
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.get(getattr(models, model_name), job_id)
        row.status = status
        for k, v in extra.items():
            setattr(row, k, v)
        db.commit()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --------------------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------------------


def test_create_normalises_target_and_rejects_duplicates(app_with_sqlite: TestClient):
    r = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["target"] == MG_SCOPE
    assert body["enabled"] is True
    assert body["last_run_at"] is None

    dup = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG_SCOPE, "every_hours": 6}
    )
    assert dup.status_code == 409

    pull = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": SUB, "every_hours": 6}
    )
    assert pull.status_code == 201
    assert pull.json()["target"] == SUB_LOWER

    listed = app_with_sqlite.get("/api/schedules").json()
    assert [s["kind"] for s in listed] == ["activity_pull", "snapshot"]


def test_create_validates_input(app_with_sqlite: TestClient):
    bad_sub = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": "not-a-guid", "every_hours": 6}
    )
    assert bad_sub.status_code == 400
    bad_scope = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": "x/y/z", "every_hours": 6}
    )
    assert bad_scope.status_code == 400
    for every in (0, 721):
        r = app_with_sqlite.post(
            "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": every}
        )
        assert r.status_code == 422
    bad_kind = app_with_sqlite.post(
        "/api/schedules", json={"kind": "export", "target": MG, "every_hours": 6}
    )
    assert bad_kind.status_code == 422


def test_patch_and_delete(app_with_sqlite: TestClient):
    created = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "snapshot", "target": MG, "every_hours": 24, "start_now": False},
    ).json()
    sid = created["id"]

    r = app_with_sqlite.patch(f"/api/schedules/{sid}", json={"enabled": False, "every_hours": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["every_hours"] == 2
    # Never ran: the new cadence is anchored on now.
    next_run = _utc(datetime.fromisoformat(body["next_run_at"]))
    assert next_run - datetime.now(UTC) < timedelta(hours=2, minutes=1)

    assert app_with_sqlite.delete(f"/api/schedules/{sid}").status_code == 204
    assert app_with_sqlite.get("/api/schedules").json() == []
    assert app_with_sqlite.delete(f"/api/schedules/{sid}").status_code == 404
    assert app_with_sqlite.patch(f"/api/schedules/{sid}", json={}).status_code == 404


# --------------------------------------------------------------------------------------
# Scheduler tick
# --------------------------------------------------------------------------------------


def test_tick_starts_due_schedules_only(app_with_sqlite: TestClient):
    due = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 12}
    ).json()
    app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "snapshot", "target": "mg-later", "every_hours": 12, "start_now": False},
    )
    paused = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": "mg-paused", "every_hours": 12}
    ).json()
    app_with_sqlite.patch(f"/api/schedules/{paused['id']}", json={"enabled": False})

    now = datetime.now(UTC) + timedelta(seconds=1)
    started = _tick(now)
    assert [j.kind for j in started] == ["snapshot"]

    s = _schedule(due["id"])
    assert s.last_job_id == started[0].job_id
    assert _utc(s.last_run_at) == now
    assert _utc(s.next_run_at) == now + timedelta(hours=12)
    assert s.last_error is None

    # Not due again until the interval passes.
    assert _tick(now + timedelta(hours=1)) == []

    jobs = app_with_sqlite.get("/api/jobs").json()
    assert [(j["kind"], j["target"], j["status"]) for j in jobs] == [
        ("snapshot", MG_SCOPE, "pending")
    ]
    listed = {s["id"]: s for s in app_with_sqlite.get("/api/schedules").json()}
    assert listed[due["id"]]["last_job_status"] == "pending"


def test_tick_respects_free_slots(app_with_sqlite: TestClient):
    for name in ("mg-a", "mg-b", "mg-c"):
        app_with_sqlite.post(
            "/api/schedules", json={"kind": "snapshot", "target": name, "every_hours": 1}
        )
    now = datetime.now(UTC) + timedelta(seconds=1)
    assert len(_tick(now, free_slots=2)) == 2
    assert len(_tick(now, free_slots=0)) == 0
    assert len(_tick(now, free_slots=2)) == 1  # the one left over


def test_tick_skips_target_with_job_in_flight(app_with_sqlite: TestClient):
    from app.core.scheduler import BUSY_ERROR, BUSY_RETRY

    manual = app_with_sqlite.post("/api/iam-access/scans", json={"scope": MG})
    assert manual.status_code == 201
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    ).json()

    now = datetime.now(UTC) + timedelta(seconds=1)
    assert _tick(now) == []
    s = _schedule(sched["id"])
    assert s.last_error == BUSY_ERROR
    assert s.last_job_id is None
    assert _utc(s.next_run_at) == now + BUSY_RETRY

    _set_job_status("Scan", manual.json()["id"], "done")
    later = now + BUSY_RETRY
    started = _tick(later)
    assert len(started) == 1
    s = _schedule(sched["id"])
    assert s.last_error is None
    assert _utc(s.next_run_at) == later + timedelta(hours=24)


def test_pull_windows_are_incremental(app_with_sqlite: TestClient):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    sched = app_with_sqlite.post(
        "/api/schedules",
        json={
            "kind": "activity_pull",
            "target": SUB,
            "every_hours": 6,
            "initial_window_days": 30,
        },
    ).json()

    now = datetime.now(UTC) + timedelta(seconds=1)
    [first] = _tick(now)
    with SessionLocal() as db:
        pull = db.get(ActivityPull, first.job_id)
        assert pull.subscription_id == SUB_LOWER
        assert _utc(pull.start_time) == now - timedelta(days=30)
        assert _utc(pull.end_time) == now
    _set_job_status("ActivityPull", first.job_id, "done")

    # Next run overlaps the previous end by an hour.
    later = now + timedelta(hours=6)
    [second] = _tick(later)
    with SessionLocal() as db:
        pull = db.get(ActivityPull, second.job_id)
        assert _utc(pull.start_time) == now - timedelta(hours=1)
        assert _utc(pull.end_time) == later
    _set_job_status("ActivityPull", second.job_id, "failed")

    # A failed pull doesn't advance the watermark: the next run still
    # starts from the first (done) pull.
    assert app_with_sqlite.post(f"/api/schedules/{sched['id']}/run").status_code == 202
    with SessionLocal() as db:
        newest = db.scalars(select(ActivityPull).order_by(ActivityPull.id.desc()).limit(1)).one()
        assert newest.id != second.job_id
        assert _utc(newest.start_time) == now - timedelta(hours=1)

    # After a long outage the start is capped a day inside Azure's 90-day
    # retention, so a long pull's later pages stay in range.
    from app.core.scheduler import pull_window

    much_later = now + timedelta(days=200)
    assert pull_window(_schedule(sched["id"]).covered_until, 30, much_later) == (
        much_later - timedelta(days=89),
        much_later,
    )
    # Same for a first run asking for the full 90 days.
    assert pull_window(None, 90, now) == (now - timedelta(days=89), now)


def test_a_manual_pull_does_not_move_the_schedules_mark(app_with_sqlite: TestClient):
    """After an outage, a short manual collection must not hide the gap."""
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": SUB, "every_hours": 1}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    [first] = _tick(now)
    _set_job_status("ActivityPull", first.job_id, "done")

    # Five days later someone collects only the last day by hand.
    today = now + timedelta(days=5)
    with SessionLocal() as db:
        db.add(
            ActivityPull(
                subscription_id=SUB_LOWER,
                start_time=today - timedelta(days=1),
                end_time=today,
                status="done",
            )
        )
        db.commit()

    [second] = _tick(today)
    with SessionLocal() as db:
        pull = db.get(ActivityPull, second.job_id)
        assert _utc(pull.start_time) == now - timedelta(hours=1)  # from the schedule's own mark
    assert _utc(_schedule(sched["id"]).covered_until) == now


# --------------------------------------------------------------------------------------
# Outcomes: errors stay visible, failures retry sooner, restarts interrupt
# --------------------------------------------------------------------------------------


def test_a_failed_daily_check_retries_soon_and_keeps_its_error(app_with_sqlite: TestClient):
    from app.core.scheduler import RETRY_AFTER

    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    [job] = _tick(now)
    _set_job_status("Scan", job.job_id, "failed", error="ScanInterrupted: could not read X")

    assert _tick(now + timedelta(minutes=1)) == []  # settles the failure, nothing due yet
    s = _schedule(sched["id"])
    assert (s.failures, s.last_error) == (1, "ScanInterrupted: could not read X")
    assert _utc(s.next_run_at) == now + timedelta(minutes=1) + RETRY_AFTER[0]

    # The retry starts on time; the error stays until a run succeeds.
    retry_at = _utc(s.next_run_at)
    [again] = _tick(retry_at)
    s = _schedule(sched["id"])
    assert s.last_error == "ScanInterrupted: could not read X"
    assert _utc(s.next_run_at) == retry_at + timedelta(hours=24)
    listed = app_with_sqlite.get("/api/schedules").json()[0]
    assert (listed["failures"], listed["last_error"]) == (1, "ScanInterrupted: could not read X")

    # Second failure in a row: one hour. Third and later: four.
    _set_job_status("Scan", again.job_id, "failed", error="still down")
    _tick(retry_at + timedelta(minutes=1))
    s = _schedule(sched["id"])
    assert (
        s.failures == 2 and _utc(s.next_run_at) == retry_at + timedelta(minutes=1) + RETRY_AFTER[1]
    )

    [third] = _tick(_utc(s.next_run_at))
    _set_job_status("Scan", third.job_id, "done")
    _tick(_utc(s.next_run_at) + timedelta(minutes=1))
    s = _schedule(sched["id"])
    assert (s.failures, s.last_error) == (0, None)


def test_an_hourly_schedule_is_not_delayed_by_the_retry_rule(app_with_sqlite: TestClient):
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": SUB, "every_hours": 1}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    for n in (1, 2, 3):
        [job] = _tick(now)
        _set_job_status("ActivityPull", job.job_id, "failed", error="down")
        _tick(now + timedelta(minutes=1))
        s = _schedule(sched["id"])
        assert s.failures == n
        # 15 min after the first failure; afterwards never later than the normal hour.
        assert _utc(s.next_run_at) <= now + timedelta(hours=1)
        now = _utc(s.next_run_at)


def test_a_paused_schedule_records_the_failure_but_is_not_brought_forward(
    app_with_sqlite: TestClient,
):
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    [job] = _tick(now)
    app_with_sqlite.patch(f"/api/schedules/{sched['id']}", json={"enabled": False})
    _set_job_status("Scan", job.job_id, "failed", error="boom")
    _tick(now + timedelta(minutes=1))
    s = _schedule(sched["id"])
    assert s.failures == 1 and _utc(s.next_run_at) == now + timedelta(hours=24)


def test_a_finished_run_is_shown_healthy_before_the_scheduler_records_it(
    app_with_sqlite: TestClient,
):
    """The page showed "Failing" next to a "done" retry until the next tick."""
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": SUB, "every_hours": 1}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    [job] = _tick(now)
    _set_job_status("ActivityPull", job.job_id, "failed", error="down")
    _tick(now + timedelta(minutes=1))  # records the failure, plans the retry
    s = _schedule(sched["id"])
    assert s.failures == 1
    now = _utc(s.next_run_at)
    [retry] = _tick(now)

    _set_job_status("ActivityPull", retry.job_id, "done")
    [listed] = [s for s in app_with_sqlite.get("/api/schedules").json() if s["id"] == sched["id"]]
    assert listed["last_job_status"] == "done"
    assert (listed["failures"], listed["last_error"], listed["last_error_summary"]) == (
        0,
        None,
        None,
    )
    assert _schedule(sched["id"]).failures == 1  # reading writes nothing

    _tick(now + timedelta(minutes=1))
    s = _schedule(sched["id"])
    assert (s.failures, s.last_error) == (0, None)


def test_jobs_interrupted_by_a_restart_run_again_without_counting_as_failures(
    app_with_sqlite: TestClient,
):
    from app.core import scheduler
    from app.db.models import ActivityPull, Scan
    from app.db.session import SessionLocal

    check = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    ).json()
    pull = app_with_sqlite.post(
        "/api/schedules", json={"kind": "activity_pull", "target": SUB, "every_hours": 1}
    ).json()
    now = datetime.now(UTC) + timedelta(seconds=1)
    started = {j.kind: j.job_id for j in _tick(now)}
    _set_job_status("Scan", started["snapshot"], "running")

    # "Restart": everything created before the new process is orphaned.
    with SessionLocal() as db:
        assert scheduler.recover_interrupted(db, datetime.now(UTC) + timedelta(seconds=5)) == 2
        assert db.get(Scan, started["snapshot"]).error == scheduler.INTERRUPTED_ERROR
        assert db.get(ActivityPull, started["activity_pull"]).status == "failed"
        # Rows of the new process are left alone.
        assert scheduler.recover_interrupted(db, datetime.now(UTC) - timedelta(hours=1)) == 0

    later = now + timedelta(minutes=2)
    again = _tick(later)
    assert sorted(j.kind for j in again) == ["activity_pull", "snapshot"]
    for sid in (check["id"], pull["id"]):
        s = _schedule(sid)
        assert (s.failures, s.last_error) == (0, None)


# --------------------------------------------------------------------------------------
# Run now
# --------------------------------------------------------------------------------------


def test_run_now_starts_job_and_resets_cadence(app_with_sqlite: TestClient):
    sched = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "snapshot", "target": MG, "every_hours": 24, "start_now": False},
    ).json()

    r = app_with_sqlite.post(f"/api/schedules/{sched['id']}/run")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["last_job_id"] is not None
    assert body["last_job_status"] == "pending"
    next_run = _utc(datetime.fromisoformat(body["next_run_at"]))
    assert next_run - datetime.now(UTC) > timedelta(hours=23)

    # Second press while that job is in flight: 409, schedule untouched.
    again = app_with_sqlite.post(f"/api/schedules/{sched['id']}/run")
    assert again.status_code == 409
    s = _schedule(sched["id"])
    assert s.last_error is None
    assert _utc(s.next_run_at) == next_run

    assert app_with_sqlite.post("/api/schedules/9999/run").status_code == 404


def test_tick_once_dispatches_to_workers_within_the_slot_budget(
    app_with_sqlite: TestClient, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.core import scheduler
    from app.core.config import settings

    release = Event()
    ran: list[tuple[str, int]] = []

    def fake_worker_for(kind):
        def run(job_id):
            ran.append((kind, job_id))
            release.wait(5)

        return run

    monkeypatch.setattr(scheduler, "worker_for", fake_worker_for)
    monkeypatch.setattr(settings, "scheduler_max_concurrent_jobs", 1)
    for name in ("mg-a", "mg-b"):
        app_with_sqlite.post(
            "/api/schedules", json={"kind": "snapshot", "target": name, "every_hours": 1}
        )

    running: set = set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        scheduler._tick_once(executor, running)
        assert len(running) == 1
        scheduler._tick_once(executor, running)  # slot still busy: nothing claimed
        assert len(running) == 1
        release.set()
        for f in list(running):
            f.result(timeout=5)
        scheduler._tick_once(executor, running)
        for f in list(running):
            f.result(timeout=5)
    assert [kind for kind, _ in ran] == ["snapshot", "snapshot"]
    assert len({job_id for _, job_id in ran}) == 2


# --------------------------------------------------------------------------------------
# What the page needs: who paused, what started a run, failures in words, filters
# --------------------------------------------------------------------------------------


def test_pausing_records_when_and_resuming_clears_it(app_with_sqlite: TestClient):
    sched = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "snapshot", "target": MG, "every_hours": 24, "start_now": False},
    ).json()
    assert sched["paused_at"] is None

    paused = app_with_sqlite.patch(f"/api/schedules/{sched['id']}", json={"enabled": False}).json()
    assert (
        paused["paused_at"] is not None and paused["paused_by"] is None
    )  # sign-in is off: nobody to name
    again = app_with_sqlite.patch(f"/api/schedules/{sched['id']}", json={"enabled": False}).json()
    assert again["paused_at"] == paused["paused_at"]  # already off: the date does not move

    resumed = app_with_sqlite.patch(f"/api/schedules/{sched['id']}", json={"enabled": True}).json()
    assert resumed["paused_at"] is None and resumed["enabled"] is True


def test_pausing_names_the_signed_in_person(app_with_sqlite: TestClient, signed_in):
    admin = signed_in("admin-id", admin=True)
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}, headers=admin
    ).json()
    paused = app_with_sqlite.patch(
        f"/api/schedules/{sched['id']}", json={"enabled": False}, headers=admin
    ).json()
    assert paused["paused_by"]


def test_jobs_say_what_started_them_and_can_be_filtered(app_with_sqlite: TestClient):
    sched = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": MG, "every_hours": 24}
    ).json()
    [scheduled] = _tick(datetime.now(UTC) + timedelta(seconds=1))
    _set_job_status(
        "Scan",
        scheduled.job_id,
        "failed",
        error="ClientAuthenticationError: Authentication failed: [SSL: UNEXPECTED_EOF_WHILE_READING]",
    )
    manual = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-other"}).json()
    now = datetime.now(UTC)
    pull = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB,
            "start": (now - timedelta(hours=2)).isoformat(),
            "end": now.isoformat(),
        },
    ).json()

    jobs = {(j["kind"], j["id"]): j for j in app_with_sqlite.get("/api/jobs").json()}
    assert jobs[("snapshot", scheduled.job_id)]["schedule_id"] == sched["id"]
    assert jobs[("snapshot", scheduled.job_id)]["origin"] == "schedule"
    assert (
        jobs[("snapshot", manual["id"])]["schedule_id"],
        jobs[("snapshot", manual["id"])]["origin"],
    ) == (None, "manual")
    assert jobs[("activity_pull", pull["id"])]["origin"] == "manual"

    # "Run now" on a schedule is a person's doing, even though the schedule's settings are used.
    other = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "snapshot", "target": "mg-run-now", "every_hours": 24, "start_now": False},
    ).json()
    ran = app_with_sqlite.post(f"/api/schedules/{other['id']}/run").json()
    by_hand = next(
        j
        for j in app_with_sqlite.get("/api/jobs").json()
        if j["id"] == ran["last_job_id"] and j["kind"] == "snapshot"
    )
    assert (by_hand["origin"], by_hand["schedule_id"]) == ("manual", other["id"])
    assert jobs[("snapshot", scheduled.job_id)]["error_summary"].startswith("Could not reach Azure")
    assert jobs[("snapshot", manual["id"])]["error_summary"] is None

    failed = app_with_sqlite.get("/api/jobs", params={"status": "failed"}).json()
    assert [(j["kind"], j["id"]) for j in failed] == [("snapshot", scheduled.job_id)]
    checks = app_with_sqlite.get("/api/jobs", params={"kind": "snapshot"}).json()
    assert {j["id"] for j in checks} == {scheduled.job_id, manual["id"], ran["last_job_id"]}
    pulls = app_with_sqlite.get("/api/jobs", params={"kind": "activity_pull"}).json()
    assert [j["id"] for j in pulls] == [pull["id"]]
    assert app_with_sqlite.get("/api/jobs", params={"kind": "nonsense"}).status_code == 422

    # The schedule says why in words once the failure is settled.
    _tick(datetime.now(UTC) + timedelta(minutes=1))
    listed = next(x for x in app_with_sqlite.get("/api/schedules").json() if x["id"] == sched["id"])
    assert listed["last_error_summary"].startswith("Could not reach Azure")
