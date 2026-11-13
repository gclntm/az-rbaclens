"""Phase 13: the assessment report, its control thresholds, the annex and saved assessments."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import report_controls as rc
from app.schemas.report import ReportChanges, ReportReviews
from tests.test_registry import MG, SUB_SCOPE
from tests.test_reviews import NOV, _open, _world


def _numbers(**over):
    base = {
        "standing_admin_high": 0,
        "standing_admin_guests": 0,
        "keys_high": 0,
        "keys_on_mg": 0,
        "orphans": 0,
        "unknown_member_groups": 0,
        "guests_with_power": 0,
        "least_privilege": 0,
    }
    return {**base, **over}


def _reviews(**over) -> ReportReviews:
    base = {
        "subscriptions": 2,
        "subscriptions_with_responsible": 2,
        "without_responsible": [],
        "rounds_this_period": 1,
        "items_this_period": 10,
        "decided_this_period": 10,
        "last_period": "2026-10",
        "last_period_on_time": True,
        "todos_open": 0,
        "todos_overdue": 0,
        "todos_done": 0,
        "todos_avg_days_to_close": None,
    }
    return ReportReviews(**{**base, **over})


def _changes(**over) -> ReportChanges:
    base = {
        "days": 30,
        "given": 3,
        "removed": 1,
        "powerful_given": 0,
        "unexplained": 0,
        "check_age_hours": 5.0,
        "subscriptions_collected_hourly": 2,
        "activity_gap_hours": 1.0,
        "subscriptions_not_collected": [],
    }
    return ReportChanges(**{**base, **over})


# ---- thresholds ---------------------------------------------------------------------------


def test_privileged_access_thresholds():
    assert rc.privileged(_numbers()).status == "met"
    assert rc.privileged(_numbers(standing_admin_high=3)).status == "partly"
    assert rc.privileged(_numbers(standing_admin_high=4)).status == "not_met"
    assert (
        rc.privileged(_numbers(standing_admin_high=1, standing_admin_guests=1)).status == "not_met"
    )


def test_identity_thresholds_depend_on_whether_orphans_are_being_handled():
    from app.schemas.report import ReportGrant

    orphan = ReportGrant(
        grant_id="g1",
        principal_id="x",
        principal_name=None,
        kind="person",
        role_name="Reader",
        scope=SUB_SCOPE,
        scope_name="prod",
        scope_level="subscription",
        how="standing",
        advice_rule="R01 orphan",
        advice_title="t",
        advice_weight="must",
    )
    assert rc.identities(_numbers(), [], set()).status == "met"
    assert rc.identities(_numbers(orphans=1), [orphan], set()).status == "not_met"
    assert (
        rc.identities(_numbers(orphans=1), [orphan], {"g1"}).status == "partly"
    )  # decided in a review
    assert rc.identities(_numbers(unknown_member_groups=2), [], set()).status == "partly"


def test_least_privilege_keys_reviews_and_logging_thresholds():
    assert [rc.least_privilege(_numbers(least_privilege=n)).status for n in (0, 9, 10)] == [
        "met",
        "partly",
        "not_met",
    ]
    assert rc.authentication(_numbers(keys_high=2)).status == "partly"
    assert rc.authentication(_numbers(keys_high=2, keys_on_mg=1)).status == "not_met"
    assert rc.access_rights(_reviews()).status == "met"
    assert rc.access_rights(_reviews(last_period_on_time=None)).status == "partly"
    assert (
        rc.access_rights(_reviews(last_period_on_time=False, todos_overdue=2)).status == "not_met"
    )
    assert rc.logging_control(_changes(), 2).status == "met"
    assert rc.logging_control(_changes(activity_gap_hours=30.0), 2).status == "partly"
    assert rc.logging_control(_changes(check_age_hours=None), 2).status == "not_met"
    assert (
        rc.logging_control(
            _changes(subscriptions_not_collected=["prod"], activity_gap_hours=None), 2
        ).status
        == "not_met"
    )


# ---- the report on the seeded world ---------------------------------------------------------


def test_tenant_report(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    _open(NOV)
    r = app_with_sqlite.get("/api/report").json()
    assert (r["scope_id"], r["scope_name"], r["grants"], r["identities"]) == (
        None,
        "The whole tenant",
        3,
        3,
    )
    statuses = {c["control"]: c["status"] for c in r["controls"]}
    assert list(statuses) == ["8.2", "5.16", "5.15", "5.17", "5.18", "8.15"]
    # sp-1: an app (not Microsoft's) holding Owner, standing, on the management group.
    assert statuses["8.2"] == "partly" and r["numbers"]["standing_admin_high"] == 1
    assert [g["principal_id"] for g in r["privileged"]] == ["sp-1"]
    assert statuses["5.15"] == "partly"  # R09: an app that can change access
    assert statuses["8.15"] == "not_met"  # no Activity Log collected for the subscription
    assert r["recommendations"][0]["rule"] == "R09 app-owner"
    assert (
        r["summary"][0]
        == "1 grant lets an app change who has access; replace it with the least role the app needs."
    )
    assert r["reviews"]["subscriptions_with_responsible"] == 1  # Marc on the subscription


def test_subscription_report_holds_only_what_was_given_there_or_inside(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    r = app_with_sqlite.get("/api/report", params={"scope": SUB_SCOPE}).json()
    assert (r["scope_name"], r["scope_level"], r["grants"]) == ("prod", "subscription", 2)
    assert (
        r["privileged"] == [] and {c["control"]: c["status"] for c in r["controls"]}["8.2"] == "met"
    )
    assert (
        app_with_sqlite.get("/api/report", params={"scope": "/subscriptions/nope"}).status_code
        == 404
    )


def test_annex_lists_every_grant_with_its_advice(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    lines = app_with_sqlite.get("/api/report/annex", params={"scope": MG}).text.splitlines()
    assert lines[0].startswith("place,place_level,who,who_type,object_id,role,can,how")
    assert len(lines) == 4 and any("R09 app-owner" in line and "A.8.2" in line for line in lines)


def test_save_and_list_assessments(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    saved = app_with_sqlite.post(
        "/api/report/assessments", json={"note": "Before the clean-up"}
    ).json()
    assert saved["scope_id"] is None and saved["statuses"]["8.2"] == "partly"
    assert saved["numbers"]["standing_admin_high"] == 1 and saved["note"] == "Before the clean-up"
    app_with_sqlite.post("/api/report/assessments", json={"scope": SUB_SCOPE})
    assert [a["id"] for a in app_with_sqlite.get("/api/report/assessments").json()] == [saved["id"]]
    assert (
        len(app_with_sqlite.get("/api/report/assessments", params={"scope": SUB_SCOPE}).json()) == 1
    )


def test_signed_in_people_get_reports_for_their_places_only(app_with_sqlite: TestClient, signed_in):
    _world(app_with_sqlite)
    marc = signed_in("marc-id")
    assert app_with_sqlite.get("/api/report", headers=marc).status_code == 403  # the whole tenant
    assert (
        app_with_sqlite.get("/api/report", params={"scope": SUB_SCOPE}, headers=marc).status_code
        == 200
    )
    assert app_with_sqlite.get("/api/report", params={"scope": MG}, headers=marc).status_code == 403
    assert (
        app_with_sqlite.post(
            "/api/report/assessments", json={"scope": SUB_SCOPE}, headers=marc
        ).status_code
        == 403
    )
    adm = signed_in("admin-id", admin=True)
    assert app_with_sqlite.get("/api/report", headers=adm).status_code == 200
