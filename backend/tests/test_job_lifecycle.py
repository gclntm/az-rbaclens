"""Job lifecycle: in-flight uniqueness, failure path, latest-scan selection,
cascade delete. Covers the P0 items in docs/redesign-plan.md § 3.

``run_scan`` / ``run_pull`` are exercised directly with their Azure
calls monkey-patched, so the DB transitions are what is under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

MG = "mg-lifecycle"
MG_SCOPE = f"/providers/microsoft.management/managementgroups/{MG}"
SUB = "8a3f1234-1111-2222-3333-aabbccddeeff"


def _set_status(scan_id: int, status: str) -> None:
    from app.db.models import Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        scan.status = status
        db.commit()


# --------------------------------------------------------------------------------------
# One in-flight job per target
# --------------------------------------------------------------------------------------


def test_second_scan_of_inflight_scope_is_409(app_with_sqlite: TestClient):
    first = app_with_sqlite.post("/api/iam-access/scans", json={"scope": MG})
    assert first.status_code == 201

    second = app_with_sqlite.post("/api/iam-access/scans", json={"scope": MG})
    assert second.status_code == 409
    assert "already pending or running" in second.json()["detail"]

    # Once the first one finishes, a new scan of the same scope is fine.
    _set_status(first.json()["id"], "done")
    third = app_with_sqlite.post("/api/iam-access/scans", json={"scope": MG})
    assert third.status_code == 201
    assert third.json()["id"] != first.json()["id"]


def test_second_pull_of_inflight_subscription_is_409(app_with_sqlite: TestClient):
    now = datetime.now(UTC)
    payload = {
        "subscription_id": SUB,
        "start": (now - timedelta(days=1)).isoformat(),
        "end": now.isoformat(),
    }
    first = app_with_sqlite.post("/api/activity/pulls", json=payload)
    assert first.status_code == 201
    second = app_with_sqlite.post("/api/activity/pulls", json=payload)
    assert second.status_code == 409


# --------------------------------------------------------------------------------------
# Worker failure path
# --------------------------------------------------------------------------------------


@pytest.fixture
def pending_scan(app_with_sqlite: TestClient) -> int:
    return app_with_sqlite.post("/api/iam-access/scans", json={"scope": MG}).json()["id"]


def _fake_collect(n_assignments: int):
    from app.db.models import RoleAssignment

    def collect(scope, scope_level, credential, on_progress):
        on_progress(1, 1, "listing")
        ras = [
            RoleAssignment(
                scope=scope,
                scope_level=scope_level,
                principal_id=f"p{i}",
                principal_type="User",
                role_name="Reader",
                role_definition_id="/providers/Microsoft.Authorization/roleDefinitions/rd",
            )
            for i in range(n_assignments)
        ]
        return ras, {scope: {"name": MG, "level": scope_level, "parent": None}}, {}, {}

    return collect


def _ra_count(scan_id: int) -> int:
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return db.scalar(
            select(func.count(RoleAssignment.id)).where(RoleAssignment.scan_id == scan_id)
        )


def test_run_scan_success_marks_done_with_heartbeat(
    pending_scan: int, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", _fake_collect(3))

    tool.run_scan(pending_scan)

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert scan.status == "done"
        assert scan.error is None
        assert scan.finished_at is not None
        assert scan.heartbeat_at is not None
        assert scan.progress_message.startswith("Done — 3")
    assert _ra_count(pending_scan) == 3


def test_run_scan_failure_during_persist_rolls_back_and_marks_failed(
    pending_scan: int, monkeypatch: pytest.MonkeyPatch
):
    """A flush/persist error must not leave the scan ``running`` or half-written.

    Before the fix the ``except`` wrote ``failed`` into a session whose
    transaction had already failed; the final commit raised and the row
    stayed ``running`` until the sweeper flipped it an hour later.
    """
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", _fake_collect(5))

    def boom(session, role_definitions):
        # Simulate a failure *after* the RAs were added to the session.
        session.flush()
        raise RuntimeError("role definition upsert exploded")

    monkeypatch.setattr(tool, "upsert_role_definitions", boom)

    tool.run_scan(pending_scan)  # must not raise

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert scan.status == "failed"
        assert scan.error == "RuntimeError: role definition upsert exploded"
        assert scan.finished_at is not None
    # Rolled back: a failed scan carries no partial result set.
    assert _ra_count(pending_scan) == 0


def test_run_scan_failure_before_persist_marks_failed(
    pending_scan: int, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    monkeypatch.setattr(tool, "get_credential", lambda: object())

    def collect(*_a, **_k):
        raise PermissionError("AuthorizationFailed")

    monkeypatch.setattr(tool, "collect_assignments", collect)
    tool.run_scan(pending_scan)

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert scan.status == "failed"
        assert scan.error == "PermissionError: AuthorizationFailed"


def test_run_pull_failure_marks_failed(
    app_with_sqlite: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as tool

    now = datetime.now(UTC)
    pull_id = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB,
            "start": (now - timedelta(days=1)).isoformat(),
            "end": now.isoformat(),
        },
    ).json()["id"]

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "get_azure_transport", lambda: None)
    monkeypatch.setattr(tool, "MonitorManagementClient", lambda *a, **k: object())

    def collect(**_k):
        raise ConnectionError("Azure Monitor unreachable")

    monkeypatch.setattr(tool, "_collect_events", collect)
    tool.run_pull(pull_id)

    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert pull.status == "failed"
        assert pull.error == "ConnectionError: Azure Monitor unreachable"
        assert pull.finished_at is not None


# --------------------------------------------------------------------------------------
# The sweeper's "failed" stands against a late worker
# --------------------------------------------------------------------------------------


def _sweep_now(model) -> None:
    """What the sweeper does when a worker stops beating (negative timeout:
    every in-flight row counts as stuck)."""
    from app.core.sweeper import _sweep
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert _sweep(db, model, -1, "job") == 1


def test_late_scan_result_does_not_overwrite_sweeper_failure(
    pending_scan: int, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    collect = _fake_collect(3)

    def slow_collect(*args):
        result = collect(*args)
        _sweep_now(Scan)  # the worker went quiet for too long
        return result

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", slow_collect)
    tool.run_scan(pending_scan)

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert scan.status == "failed"
        assert scan.error.startswith("Marked failed by sweeper")
    assert _ra_count(pending_scan) == 0


def test_late_pull_result_does_not_overwrite_sweeper_failure(
    app_with_sqlite: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity as tool

    now = datetime.now(UTC)
    pull_id = app_with_sqlite.post(
        "/api/activity/pulls",
        json={
            "subscription_id": SUB,
            "start": (now - timedelta(days=1)).isoformat(),
            "end": now.isoformat(),
        },
    ).json()["id"]

    def collect(**_k):
        _sweep_now(ActivityPull)
        return 0, 0

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "get_azure_transport", lambda: None)
    monkeypatch.setattr(tool, "MonitorManagementClient", lambda *a, **k: object())
    monkeypatch.setattr(tool, "_collect_events", collect)
    tool.run_pull(pull_id)

    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert pull.status == "failed"
        assert pull.error.startswith("Marked failed by sweeper")


def test_worker_does_not_start_a_job_swept_while_pending(
    pending_scan: int, monkeypatch: pytest.MonkeyPatch
):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    _sweep_now(Scan)

    def never(*_a, **_k):
        raise AssertionError("a swept job must not run")

    monkeypatch.setattr(tool, "get_credential", never)
    monkeypatch.setattr(tool, "collect_assignments", never)
    tool.run_scan(pending_scan)

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert scan.status == "failed"
        assert scan.started_at is None


# --------------------------------------------------------------------------------------
# Latest scan per root
# --------------------------------------------------------------------------------------


def test_latest_scan_per_root_ignores_failed_and_running(app_with_sqlite: TestClient):
    from app.core.scan_coverage import latest_done_scan_ids
    from app.db.models import Scan
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        old_done = Scan(
            scope="/subscriptions/a",
            scope_level="subscription",
            status="done",
            created_at=now - timedelta(days=2),
        )
        newer_failed = Scan(
            scope="/subscriptions/a",
            scope_level="subscription",
            status="failed",
            created_at=now - timedelta(days=1),
        )
        newest_running = Scan(
            scope="/subscriptions/a",
            scope_level="subscription",
            status="running",
            created_at=now,
        )
        # Tie on created_at for another root: higher id wins.
        tie_1 = Scan(
            scope="/subscriptions/b",
            scope_level="subscription",
            status="done",
            created_at=now - timedelta(hours=1),
        )
        tie_2 = Scan(
            scope="/subscriptions/b",
            scope_level="subscription",
            status="done",
            created_at=now - timedelta(hours=1),
        )
        db.add_all([old_done, newer_failed, newest_running, tie_1, tie_2])
        db.commit()
        latest = latest_done_scan_ids(db)
        assert latest == {old_done.id, tie_2.id}


# --------------------------------------------------------------------------------------
# Cascade delete without ORM row loading
# --------------------------------------------------------------------------------------


def test_delete_scan_cascades_children_at_db_level(app_with_sqlite: TestClient):
    from app.db.models import GroupMembership, RoleAssignment, Scan, ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan = Scan(scope="/subscriptions/c", scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/c",
                    scope_level="subscription",
                    principal_id="g1",
                    principal_type="Group",
                    role_name="Reader",
                    role_definition_id="rd",
                ),
                GroupMembership(scan_id=scan.id, group_id="g1", member_id="u1"),
                ScanGroupResolution(scan_id=scan.id, group_id="g1"),
            ]
        )
        db.commit()
        scan_id = scan.id

    assert app_with_sqlite.delete(f"/api/iam-access/scans/{scan_id}").status_code == 204

    with SessionLocal() as db:
        for model in (RoleAssignment, GroupMembership, ScanGroupResolution):
            assert (
                db.scalar(select(func.count()).select_from(model).where(model.scan_id == scan_id))
                == 0
            )
