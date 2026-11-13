"""Changes feed, daily-aligned schedules, default schedules, freshness."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.findings import grant_fingerprint
from app.core.scheduler import next_run
from tests.test_registry import (
    MG,
    OWNER,
    READER,
    RG,
    SUB,
    SUB_SCOPE,
    _arm,
    _ra,
    _seed_roles,
    _seed_scan,
)

RA_ID = f"{RG}/providers/Microsoft.Authorization/roleAssignments/aaaa"
GONE_ID = f"{SUB_SCOPE}/providers/Microsoft.Authorization/roleAssignments/bbbb"


# ---- next_run ---------------------------------------------------------------------------


@pytest.fixture
def tz(monkeypatch: pytest.MonkeyPatch):
    def set_tz(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield set_tz
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def test_next_run_plain_interval():
    now = datetime(2026, 11, 8, 5, 30, tzinfo=UTC)
    assert next_run(1, None, now) == now + timedelta(hours=1)


def test_next_run_aligns_to_hour_in_container_time(tz):
    tz("UTC")
    assert next_run(24, 6, datetime(2026, 11, 8, 5, 30, tzinfo=UTC)) == datetime(
        2026, 11, 8, 6, tzinfo=UTC
    )
    assert next_run(24, 6, datetime(2026, 11, 8, 6, 0, tzinfo=UTC)) == datetime(
        2026, 11, 9, 6, tzinfo=UTC
    )
    tz("Europe/Zurich")
    # 06:00 in Zurich is 05:00 UTC in winter...
    assert next_run(24, 6, datetime(2026, 11, 8, 12, 0, tzinfo=UTC)) == datetime(
        2026, 11, 9, 5, tzinfo=UTC
    )
    # ...and 04:00 UTC once summer time starts (29 March 2026).
    assert next_run(24, 6, datetime(2026, 3, 28, 12, 0, tzinfo=UTC)) == datetime(
        2026, 3, 29, 4, tzinfo=UTC
    )


# ---- changes between two checks -----------------------------------------------------------


def _two_checks(client: TestClient) -> tuple[int, int]:
    """Check A: Owner@MG sp-1, Reader@sub alice (assignment GONE_ID), group grp-1 (alice).
    Check B: alice's Reader removed; bob gets Reader@rg (RA_ID, with an Activity Log
    event by sp-tf); carol gets Owner@sub, created by sp-tf after A; bob joins grp-1."""
    from app.db.models import ActivityEvent, GroupMembership, ScanGroupResolution
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        a = _seed_scan(db, offset_s=-3600)
        alice = _ra(
            a.id, SUB_SCOPE, "subscription", "alice-id", "User", READER, "alice@example.com"
        )
        alice.raw = {"id": GONE_ID}
        db.add_all(
            [
                _ra(a.id, MG, "mgmt_group", "sp-1", "ServicePrincipal", OWNER, "sp-one"),
                alice,
                _ra(a.id, RG, "resource_group", "grp-1", "Group", READER, "grp-readers"),
                ScanGroupResolution(scan_id=a.id, group_id="grp-1"),
                GroupMembership(
                    scan_id=a.id,
                    group_id="grp-1",
                    member_id="alice-id",
                    member_type="User",
                    member_name="alice@example.com",
                ),
            ]
        )
        b = _seed_scan(db)
        bob = _ra(b.id, RG, "resource_group", "bob-id", "User", READER, "bob@example.com")
        bob.raw = {"id": RA_ID}
        carol = _ra(b.id, SUB_SCOPE, "subscription", "carol-id", "User", OWNER, "carol@example.com")
        carol.created_on = datetime.now(UTC) - timedelta(minutes=30)
        carol.created_by = "sp-tf"
        carol.created_by_name = "sp-terraform"
        db.add_all(
            [
                _ra(b.id, MG, "mgmt_group", "sp-1", "ServicePrincipal", OWNER, "sp-one"),
                bob,
                carol,
                _ra(b.id, RG, "resource_group", "grp-1", "Group", READER, "grp-readers"),
                ScanGroupResolution(scan_id=b.id, group_id="grp-1"),
                GroupMembership(
                    scan_id=b.id,
                    group_id="grp-1",
                    member_id="alice-id",
                    member_type="User",
                    member_name="alice@example.com",
                ),
                GroupMembership(
                    scan_id=b.id,
                    group_id="grp-1",
                    member_id="bob-id",
                    member_type="User",
                    member_name="bob@example.com",
                ),
                ActivityEvent(
                    subscription_id=SUB,
                    event_data_id="ev-write",
                    raw={},
                    operation_name="Microsoft.Authorization/roleAssignments/write",
                    status="Succeeded",
                    resource_id=RA_ID.upper(),
                    caller_oid="sp-tf",
                    occurred_at=datetime.now(UTC) - timedelta(minutes=20),
                ),
            ]
        )
        db.commit()
        ids = (a.id, b.id)
    client.post("/api/settings/registry/rebuild")
    return ids


def test_changes_between_checks_with_best_time_and_author(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.models import AccessChange
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        assert record_changes_for_scan(db, b) == 4
        assert record_changes_for_scan(db, b) == 0  # already recorded
        got = {(c.kind, c.principal_id): c for c in db.query(AccessChange).all()}
        db.expunge_all()

    bob = got[("granted", "bob-id")]
    assert (bob.source, bob.actor_id, bob.level, bob.role_name) == (
        "activity_log",
        "sp-tf",
        "view",
        "Reader",
    )
    assert bob.subject_key == grant_fingerprint(RG, _arm(READER), "bob-id")
    carol = got[("granted", "carol-id")]
    assert (carol.source, carol.actor_id, carol.actor_name, carol.level) == (
        "created_on",
        "sp-tf",
        "sp-terraform",
        "access",
    )
    alice = got[("removed", "alice-id")]
    assert (alice.source, alice.actor_id, alice.azure_id) == ("daily_check", None, GONE_ID.lower())
    joined = got[("member_added", "bob-id")]
    assert (joined.via_group_id, joined.via_group_name, joined.level) == (
        "grp-1",
        "grp-readers",
        "view",
    )


def test_first_check_records_nothing_and_rebuild_replays_history(app_with_sqlite: TestClient):
    from app.core.changes import rebuild_changes, record_changes_for_scan
    from app.db.session import SessionLocal

    a, _ = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        assert record_changes_for_scan(db, a) == 0
        assert rebuild_changes(db) == 4


def test_late_delete_event_upgrades_first_seen_removal(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan, refine_from_events
    from app.db.models import AccessChange, ActivityEvent
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        record_changes_for_scan(db, b)
        db.add(
            ActivityEvent(
                subscription_id=SUB,
                event_data_id="ev-del",
                raw={},
                operation_name="Microsoft.Authorization/roleAssignments/delete",
                status="Succeeded",
                resource_id=GONE_ID,
                caller_oid="sp-1",
                occurred_at=datetime.now(UTC) - timedelta(minutes=50),
            )
        )
        db.commit()
        assert refine_from_events(db, SUB) == 1
        alice = db.query(AccessChange).filter_by(kind="removed").one()
        assert (alice.source, alice.actor_id, alice.actor_name) == (
            "activity_log",
            "sp-1",
            "sp-one",
        )


def test_changes_endpoint_filters_place_direction_and_level(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        record_changes_for_scan(db, b)

    everything = app_with_sqlite.get("/api/changes").json()
    assert (
        everything["total"],
        everything["given"],
        everything["removed"],
        everything["powerful"],
    ) == (4, 3, 1, 1)
    assert sum(w["given"] + w["removed"] for w in everything["weeks"]) == 4
    first = everything["items"][0]
    assert first["kind"] == "member_added"  # newest: first seen at check B

    rg = app_with_sqlite.get("/api/changes", params={"scope": RG}).json()
    assert {(i["kind"], i["principal_id"]) for i in rg["items"]} == {
        ("granted", "bob-id"),
        ("member_added", "bob-id"),
    }
    assert all(i["grant_id"] is None for i in rg["items"] if i["kind"] == "member_added")

    removed = app_with_sqlite.get("/api/changes", params={"direction": "removed"}).json()
    assert [i["principal_id"] for i in removed["items"]] == ["alice-id"]
    powerful = app_with_sqlite.get("/api/changes", params={"powerful_only": "true"}).json()
    assert [i["principal_id"] for i in powerful["items"]] == ["carol-id"]
    assert (
        app_with_sqlite.get("/api/changes", params={"scope": "/subscriptions/nope"}).status_code
        == 404
    )


# ---- default schedules ---------------------------------------------------------------------


def test_default_schedules_created_once_and_deletions_stick(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core.config import settings
    from app.core.default_schedules import ensure_default_schedules
    from app.db.session import SessionLocal

    monkeypatch.setattr(settings, "default_schedules", True)
    with SessionLocal() as db:
        assert ensure_default_schedules(db, datetime.now(UTC)) == []  # nothing checked yet
    _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        created = ensure_default_schedules(db, datetime.now(UTC))
        assert sorted((s.kind, s.target, s.every_hours, s.at_hour) for s in created) == [
            ("activity_pull", SUB, 1, None),
            (
                "activity_pull",
                "management-groups",
                1,
                None,
            ),  # the management groups' logs, together
            ("snapshot", MG, 24, 6),
        ]
        assert ensure_default_schedules(db, datetime.now(UTC)) == []

    schedules = app_with_sqlite.get("/api/schedules").json()
    pull = next(s for s in schedules if s["target"] == SUB)
    assert app_with_sqlite.delete(f"/api/schedules/{pull['id']}").status_code == 204
    with SessionLocal() as db:
        assert ensure_default_schedules(db, datetime.now(UTC)) == []  # not re-created

    fresh = app_with_sqlite.get("/api/freshness").json()
    assert fresh["daily_check_at_hour"] == 6 and fresh["next_check_at"] is not None
    assert fresh["access_as_of"] is not None and fresh["last_check"] is None


def test_schedule_api_accepts_a_daily_hour_only_for_daily_schedules(app_with_sqlite: TestClient):
    bad = app_with_sqlite.post(
        "/api/schedules", json={"kind": "snapshot", "target": SUB, "every_hours": 12, "at_hour": 6}
    )
    assert bad.status_code == 422
    ok = app_with_sqlite.post(
        "/api/schedules",
        json={
            "kind": "snapshot",
            "target": SUB,
            "every_hours": 24,
            "at_hour": 6,
            "start_now": False,
        },
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["at_hour"] == 6
    patched = app_with_sqlite.patch(f"/api/schedules/{body['id']}", json={"every_hours": 12}).json()
    assert (patched["every_hours"], patched["at_hour"]) == (12, None)


def test_grants_under_a_refused_place_are_not_reported_as_removed(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.models import AccessChange, Scan
    from app.db.session import SessionLocal
    from tests.test_registry import HIERARCHY

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        # Check B was refused (403) on the subscription where alice's grant was.
        db.get(Scan, b).hierarchy = {
            **HIERARCHY,
            SUB_SCOPE: {**HIERARCHY[SUB_SCOPE], "unread": True},
        }
        db.commit()
        assert record_changes_for_scan(db, b) == 3
        kinds = {(c.kind, c.principal_id) for c in db.query(AccessChange).all()}
    assert ("removed", "alice-id") not in kinds and ("granted", "bob-id") in kinds
