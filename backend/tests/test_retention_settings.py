"""What is kept: saved values over the server's (core/retention_settings.py), and
retention applying them (core/retention.py, plan "Data kept", steps 1-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import retention_settings as rs
from app.core.retention import snapshots_to_prune
from tests.test_retention import NOW, SUB, _snap


def test_the_server_values_apply_until_an_admin_saves_one(app_with_sqlite: TestClient, monkeypatch):
    from app.core.config import settings
    from app.db.session import SessionLocal

    monkeypatch.setattr(settings, "event_retention_days", 100)
    monkeypatch.setattr(settings, "entra_audit_retention_days", None)
    with SessionLocal() as db:
        now = rs.effective(db)
        assert (now.event_retention_days, now.entra_audit_retention_days) == (100, 100)
        assert (now.snapshot_keep_days, now.snapshot_keep_per_scope, now.compact_after_days) == (
            0,
            6,
            4,
        )
        assert set(rs.sources(db).values()) == {"server"}

        before, after = rs.save(db, {"snapshot_keep_days": 365, "entra_audit_retention_days": 30})
        db.commit()
        assert (before["snapshot_keep_days"], after["snapshot_keep_days"]) == (0, 365)
        assert rs.effective(db).entra_audit_retention_days == 30
        assert rs.sources(db)["snapshot_keep_days"] == "saved"

        rs.save(db, {"snapshot_keep_days": None})  # cleared: back to the server's
        db.commit()
        assert rs.effective(db).snapshot_keep_days == 0
        assert rs.sources(db)["snapshot_keep_days"] == "server"


@pytest.mark.parametrize(
    "values",
    [
        {"snapshot_keep_per_scope": 0},  # at least one check per place
        {"event_retention_days": 5000},
        {"compact_after_days": 2},  # collections re-read the last two days
        {"audit_retention_days": "365"},
        {"audit_retention_days": True},
        {"no_such_setting": 1},
    ],
)
def test_bad_values_are_refused(app_with_sqlite: TestClient, values):
    from app.db.session import SessionLocal

    with SessionLocal() as db, pytest.raises(rs.InvalidRetention):
        rs.save(db, values)


def test_never_fold_and_tampered_values(app_with_sqlite: TestClient):
    from app.db.models import AppSetting
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        rs.save(db, {"compact_after_days": 0})
        db.commit()
        assert rs.effective(db).compact_after_days == 0
        # Something wrote nonsense straight into the table: ignored, server values apply.
        db.get(AppSetting, rs.KEY).value = {"compact_after_days": 1, "event_retention_days": "x"}
        db.commit()
        assert rs.effective(db).compact_after_days == 4
        assert rs.effective(db).event_retention_days == rs.defaults().event_retention_days


# ---- retention applying them --------------------------------------------------------------


def test_checks_are_kept_by_time_or_by_count_whichever_keeps_more():
    scans = [_snap(i, SUB, age_days=10 - i) for i in range(1, 10)]  # ages 9 .. 1 days
    # Count only: the newest 3 stay.
    surplus, _ = snapshots_to_prune(scans, set(), NOW, keep_per_scope=3, failed_days=7)
    assert surplus == {1, 2, 3, 4, 5, 6}
    # And every check up to 5 days old: ages 1 to 5 stay, 6 to 9 go.
    surplus, _ = snapshots_to_prune(scans, set(), NOW, keep_per_scope=3, failed_days=7, keep_days=5)
    assert surplus == {1, 2, 3, 4}
    # Count wins when it keeps more.
    surplus, _ = snapshots_to_prune(scans, {1}, NOW, keep_per_scope=7, failed_days=7, keep_days=1)
    assert surplus == {2}  # 1 is still a place's current data


def test_each_log_has_its_own_cutoff_and_folding_can_be_off(app_with_sqlite: TestClient):
    from app.core.activity_compaction import days_to_compact
    from app.core.retention import run_retention
    from app.db.models import ActivityEvent, DirectoryAuditEvent
    from app.db.session import SessionLocal

    now = datetime.now(UTC)
    with SessionLocal() as db:
        for days in (20, 50):
            db.add(
                ActivityEvent(
                    subscription_id="s1",
                    event_data_id=f"ev-{days}",
                    operation_name="Microsoft.Compute/virtualMachines/start/action",
                    status="Succeeded",
                    caller="app-id",
                    caller_type="ServicePrincipal",
                    occurred_at=now - timedelta(days=days),
                )
            )
            db.add(
                DirectoryAuditEvent(
                    audit_id=f"au-{days}",
                    kind="group",
                    action="add",
                    group_id="g",
                    member_id="m",
                    occurred_at=now - timedelta(days=days),
                )
            )
        rs.save(
            db,
            {"event_retention_days": 30, "entra_audit_retention_days": 60, "compact_after_days": 0},
        )
        db.commit()
        assert days_to_compact(db, now, 0) == []  # never fold
        run_retention(db, now)
        assert [e.event_data_id for e in db.query(ActivityEvent).all()] == ["ev-20"]
        assert sorted(e.audit_id for e in db.query(DirectoryAuditEvent).all()) == ["au-20", "au-50"]


# ---- the API (step 3) -----------------------------------------------------------------------


def test_the_page_shows_what_is_kept_and_stored(app_with_sqlite: TestClient):
    body = app_with_sqlite.get("/api/settings/data-kept").json()
    assert body["values"]["snapshot_keep_per_scope"] == 6
    assert set(body["sources"].values()) == {"server"}
    assert body["bounds"]["compact_after_days"] == [3, 3650] and body["never"] == [
        "compact_after_days"
    ]
    assert set(body["stored"]) == {
        "checks",
        "failed_checks",
        "activity_events",
        "entra_audit",
        "changes",
        "audit",
        "pulls",
    }
    # A size on Postgres, none on SQLite.
    assert body["stored"]["checks"]["bytes"] is None or body["stored"]["checks"]["bytes"] > 0


def test_saving_is_validated_and_says_what_changed_in_the_audit_log(app_with_sqlite: TestClient):
    bad = app_with_sqlite.put("/api/settings/data-kept", json={"values": {"compact_after_days": 1}})
    assert bad.status_code == 422 and "compact_after_days" in bad.json()["detail"]
    ok = app_with_sqlite.put(
        "/api/settings/data-kept",
        json={"values": {"snapshot_keep_days": 365, "audit_retention_days": None}},
    )
    assert ok.status_code == 200
    assert ok.json()["values"]["snapshot_keep_days"] == 365
    assert ok.json()["sources"]["snapshot_keep_days"] == "saved"
    events = app_with_sqlite.get("/api/audit/events").json()
    saved = next(
        e
        for e in events
        if e["path"] == "/api/settings/data-kept"
        and e["method"] == "PUT"
        and e["status_code"] == 200
    )
    assert saved["detail"] == {
        "before": {"snapshot_keep_days": 0},
        "after": {"snapshot_keep_days": 365},
    }
    # The Database tab's summary reads the same values.
    assert (
        app_with_sqlite.get("/api/settings/database/retention").json()["snapshot_keep_per_scope"]
        == 6
    )


def test_the_preview_counts_without_deleting(app_with_sqlite: TestClient):
    from app.db.models import AuditEvent
    from app.db.session import SessionLocal

    old = datetime.now(UTC) - timedelta(days=40)
    with SessionLocal() as db:
        db.add_all(
            [
                AuditEvent(actor="a", method="PUT", path="/api/x", status_code=200, occurred_at=old)
                for _ in range(3)
            ]
        )
        db.commit()
    preview = app_with_sqlite.post(
        "/api/settings/data-kept/preview", json={"values": {"audit_retention_days": 30}}
    ).json()["deletes"]
    assert preview["audit"] == 3
    assert set(preview) >= {
        "checks",
        "failed_checks",
        "activity_events",
        "entra_audit",
        "changes",
        "pulls",
        "days_to_fold",
    }
    with SessionLocal() as db:
        assert (
            db.query(AuditEvent)
            .filter(AuditEvent.occurred_at < datetime.now(UTC) - timedelta(days=30))
            .count()
            == 3
        )
    assert (
        app_with_sqlite.post(
            "/api/settings/data-kept/preview", json={"values": {"x": 1}}
        ).status_code
        == 422
    )
