"""Who changed a group, from the Entra ID audit log (tools/entra_audit.py, core/changes.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.activity_targets import ENTRA_AUDIT
from app.tools import entra_audit as ea
from tests.test_changes import _two_checks

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _entry(
    audit_id: str,
    *,
    action: str = "Add member to group",
    member: str = "bob-id",
    group: str = "grp-1",
    when: datetime | None = None,
    by_user: str | None = "admin-id",
    by_app: str | None = None,
    result: str = "success",
    properties: bool = True,
) -> dict:
    """One directoryAudits entry, in the shape Graph returns."""
    props = (
        [
            {"displayName": "Group.ObjectID", "oldValue": None, "newValue": f'"{group}"'},
            {"displayName": "Group.DisplayName", "oldValue": None, "newValue": '"grp-readers"'},
        ]
        if properties
        else []
    )
    if action.startswith("Remove") and properties:
        props = [{**p, "oldValue": p["newValue"], "newValue": None} for p in props]
    return {
        "id": audit_id,
        "category": "GroupManagement",
        "activityDisplayName": action,
        "activityDateTime": (when or NOW).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "result": result,
        "initiatedBy": {
            "user": {"id": by_user, "userPrincipalName": "admin@example.org"} if by_user else None,
            "app": {"servicePrincipalId": by_app, "displayName": "MS-PIM"} if by_app else None,
        },
        "targetResources": [
            {
                "id": member,
                "type": "User",
                "userPrincipalName": "bob@example.org",
                "modifiedProperties": props,
            },
            {"id": group, "type": "Group", "displayName": "grp-readers"},
        ],
    }


class _Answer:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code, self._body = status, body or {}

    def json(self) -> dict:
        return self._body

    def text(self) -> str:
        return str(self._body)


class _Graph:
    """Stands in for the pipeline client: answers in order, remembers what was asked."""

    def __init__(self, *answers: _Answer):
        self.answers, self.asked = list(answers), []

    def send_request(self, request):
        self.asked.append(request.url)
        return self.answers.pop(0)


def _refusal(code: str) -> _Answer:
    return _Answer(403, {"error": {"code": code, "message": "no"}})


# ---- reading Graph ------------------------------------------------------------------------


def test_entries_ask_for_the_window_and_the_two_activities_and_follow_pages():
    graph = _Graph(
        _Answer(200, {"value": [_entry("a")], "@odata.nextLink": f"{ea.GRAPH}/v1.0/next"}),
        _Answer(200, {"value": [_entry("b", action="Remove member from group")]}),
    )
    got = list(ea.entries(graph, NOW - timedelta(hours=1), NOW))
    assert [e["id"] for e in got] == ["a", "b"]
    assert "activityDateTime ge 2026-11-18T11:00:00Z" in graph.asked[0]
    assert "activityDisplayName eq 'Remove member from group'" in graph.asked[0]
    assert graph.asked[1] == f"{ea.GRAPH}/v1.0/next"


def test_when_graph_rejects_the_activity_filter_the_window_alone_is_asked():
    graph = _Graph(
        _Answer(400, {"error": {"code": "BadRequest"}}),
        _Answer(200, {"value": [_entry("a"), {**_entry("x"), "activityDisplayName": "Add user"}]}),
    )
    assert [e["id"] for e in ea.entries(graph, NOW - timedelta(hours=1), NOW)] == ["a"]
    assert "activityDisplayName" not in graph.asked[1]


def test_a_window_older_than_entra_keeps_starts_at_the_earliest_time_allowed():
    too_old = _Answer(
        400,
        {
            "error": {
                "code": "UnknownError",
                "message": "Specified argument was out of the range of valid values. (Parameter "
                "'Minimum allowed time for activityDateTime is 10/18/2026 12:00:00 AM')",
            }
        },
    )
    graph = _Graph(too_old, _Answer(200, {"value": [_entry("a")]}))
    got = list(ea.entries(graph, NOW - timedelta(days=40), NOW))
    assert [e["id"] for e in got] == ["a"]
    assert "activityDateTime ge 2026-10-18T00:01:00Z" in graph.asked[1]
    assert "activityDisplayName eq" in graph.asked[1]  # still the narrow filter
    # A window entirely before it: nothing to read, no error.
    old = _Graph(too_old)
    assert (
        list(ea.entries(old, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 2, tzinfo=UTC)))
        == []
    )


def test_an_audit_page_on_another_address_is_not_followed():
    graph = _Graph(_Answer(200, {"value": [_entry("a")], "@odata.nextLink": "https://evil/x"}))
    assert len(list(ea.entries(graph, NOW - timedelta(hours=1), NOW))) == 1


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("Authentication_MSGraphPermissionMissing", ea.NO_PERMISSION),
        ("Authorization_RequestDenied", ea.NO_PERMISSION),
        ("Authentication_RequestFromNonPremiumTenantOrB2CTenant", ea.LICENCE_REQUIRED),
    ],
)
def test_a_refusal_says_whether_permission_or_licence_is_missing(code, reason):
    with pytest.raises(ea.AuditLogNotReadable) as err:
        list(ea.entries(_Graph(_refusal(code)), NOW - timedelta(hours=1), NOW))
    assert err.value.reason == reason
    assert ea.probe(_Graph(_refusal(code)), NOW) == reason
    assert ea.probe(_Graph(_Answer(200, {"value": []})), NOW) == ea.READABLE


def test_entries_are_read_with_the_group_the_member_and_who_did_it():
    added = ea.parse(_entry("a"))
    assert (added.action, added.group_id, added.group_name, added.member_id) == (
        "add",
        "grp-1",
        "grp-readers",
        "bob-id",
    )
    assert (added.initiator_type, added.initiator_id, added.initiator_name) == (
        "user",
        "admin-id",
        "admin@example.org",
    )
    removed = ea.parse(
        _entry("b", action="Remove member from group", by_user=None, by_app="PIM-SP", group="G2")
    )
    assert (removed.action, removed.group_id, removed.initiator_type) == ("remove", "g2", "app")
    assert (removed.initiator_id, removed.initiator_name) == ("pim-sp", "MS-PIM")
    # Without modified properties, the group is the target typed Group.
    bare = ea.parse(_entry("c", properties=False))
    assert (bare.group_id, bare.member_id) == ("grp-1", "bob-id")
    # Microsoft's documented shape: the group first, "Type" capitalised, no Group.ObjectID.
    documented = {
        **_entry("e"),
        "targetResources": [
            {
                "id": "GRP-1",
                "Type": "Group",
                "displayName": "grp-readers",
                "modifiedProperties": [
                    {
                        "displayName": "Action Client Name",
                        "oldValue": None,
                        "newValue": "DirectorySync",
                    }
                ],
            },
            {
                "id": "BOB-ID",
                "Type": "User",
                "userPrincipalName": "bob@example.org",
                "modifiedProperties": [],
            },
        ],
    }
    got = ea.parse(documented)
    assert (got.group_id, got.group_name, got.member_id, got.member_type) == (
        "grp-1",
        "grp-readers",
        "bob-id",
        "User",
    )
    # A group added to a group, with nothing saying which is the parent: not guessed.
    two_groups = {
        **documented,
        "targetResources": [
            {"id": "g-a", "Type": "Group", "modifiedProperties": []},
            {"id": "g-b", "Type": "Group", "modifiedProperties": []},
        ],
    }
    assert ea.parse(two_groups) is None
    assert ea.parse({**_entry("d"), "targetResources": []}) is None


# ---- collecting ---------------------------------------------------------------------------


def _run(monkeypatch, graph: _Graph) -> int:
    """One collection of the audit log against a faked Graph; returns the pull id."""
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity

    monkeypatch.setattr(activity, "get_credential", lambda: None)
    monkeypatch.setattr(activity, "get_azure_transport", lambda: None)
    monkeypatch.setattr(ea, "client", lambda *_a, **_k: graph)
    with SessionLocal() as db:
        pull = ActivityPull(
            subscription_id=ENTRA_AUDIT,
            start_time=NOW - timedelta(days=1),
            end_time=NOW,
            status="pending",
        )
        db.add(pull)
        db.commit()
        pull_id = pull.id
    activity.run_pull(pull_id)
    return pull_id


def test_a_collection_stores_each_entry_once_and_marks_the_log_readable(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.db.models import ActivityEvent, ActivityPull, DirectoryAuditEvent
    from app.db.session import SessionLocal

    page = {"value": [_entry("a"), _entry("b", member="carol-id")]}
    first = _run(monkeypatch, _Graph(_Answer(200, page)))
    second = _run(monkeypatch, _Graph(_Answer(200, page)))
    with SessionLocal() as db:
        assert sorted(e.audit_id for e in db.query(DirectoryAuditEvent).all()) == ["a", "b"]
        assert db.query(ActivityEvent).count() == 0  # never mixed with the Activity Log
        one, two = db.get(ActivityPull, first), db.get(ActivityPull, second)
        assert (one.status, one.events_persisted, two.events_persisted) == ("done", 2, 0)
        assert ea.read_status(db)["status"] == ea.READABLE


def test_a_refused_collection_fails_and_says_why(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    pull_id = _run(monkeypatch, _Graph(_refusal("Authentication_MSGraphPermissionMissing")))
    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert pull.status == "failed" and "AuditLog.Read.All" in pull.error
        assert ea.read_status(db)["status"] == ea.NO_PERMISSION
        from app.core.job_errors import explain

        assert "AuditLog.Read.All" in explain(pull.error)
        licence = str(ea.AuditLogNotReadable(ea.LICENCE_REQUIRED, "code"))
        assert "licence" in explain(f"AuditLogNotReadable: {licence}")


# ---- is it readable, and the schedule ---------------------------------------------------------


def test_the_log_is_tried_again_once_a_day_while_refused(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    refused = _Graph(*[_refusal("Authentication_MSGraphPermissionMissing")] * 2)
    with SessionLocal() as db:
        assert ea.refresh_status_if_due(db, NOW, refused) == ea.NO_PERMISSION
        assert ea.refresh_status_if_due(db, NOW + timedelta(hours=2), refused) is None
        assert ea.refresh_status_if_due(db, NOW + timedelta(hours=21), refused) == ea.NO_PERMISSION
        ok = _Graph(_Answer(200, {"value": []}))
        assert (
            ea.refresh_status_if_due(db, NOW + timedelta(hours=22), ok, force=True) == ea.READABLE
        )
        # Readable: the collections keep it current, no more tries.
        assert ea.refresh_status_if_due(db, NOW + timedelta(days=3), ok) is None


def test_graph_not_answering_is_not_a_refusal(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    class _Down:
        def send_request(self, _request):
            raise ConnectionError("no route")

    with SessionLocal() as db:
        assert ea.refresh_status_if_due(db, NOW, _Down()) == ea.UNAVAILABLE
        assert "Could not reach" in ea.read_status(db)["message"]
        assert "sign in" in ea._unavailable_message(
            type("ClientAuthenticationError", (Exception,), {})()
        )
        # Tried again at the next chance, not a day later.
        ok = _Graph(_Answer(200, {"value": []}))
        assert ea.refresh_status_if_due(db, NOW + timedelta(minutes=5), ok) == ea.READABLE


def test_the_hourly_collection_is_added_only_once_the_log_can_be_read(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core.config import settings
    from app.core.default_schedules import ensure_default_schedules
    from app.db.session import SessionLocal

    monkeypatch.setattr(settings, "default_schedules", True)
    _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        made = ensure_default_schedules(db, NOW)
        assert ENTRA_AUDIT not in {s.target for s in made}
        ea.save_status(db, ea.READABLE, NOW)
        db.commit()
        made = ensure_default_schedules(db, NOW)
        assert [(s.target, s.every_hours, s.initial_window_days) for s in made] == [
            (ENTRA_AUDIT, 1, 30)
        ]


def test_coverage_shows_the_audit_log_apart_and_check_now_tries_it(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "default_schedules", True)
    _two_checks(app_with_sqlite)
    before = app_with_sqlite.get("/api/activity/coverage").json()["entra_audit"]
    assert (before["status"], before["schedule_id"]) == (None, None)

    monkeypatch.setattr(ea, "client", lambda *_a, **_k: _Graph(_Answer(200, {"value": []})))
    monkeypatch.setattr("app.core.azure.get_credential", lambda: None)
    monkeypatch.setattr("app.core.azure.get_azure_transport", lambda: None)
    after = app_with_sqlite.post("/api/activity/entra-audit/check").json()
    assert after["status"] == ea.READABLE and after["schedule_id"] is not None
    cov = app_with_sqlite.get("/api/activity/coverage").json()
    assert ENTRA_AUDIT not in {s["subscription_id"] for s in cov["subscriptions"]}
    assert cov["entra_audit"]["enabled"] is True


def test_it_can_be_scheduled_and_collected_by_hand(app_with_sqlite: TestClient):
    made = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "activity_pull", "target": "Entra-Directory", "every_hours": 1},
    )
    assert made.status_code == 201 and made.json()["target"] == ENTRA_AUDIT
    window = {
        "start": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
    }
    once = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": ENTRA_AUDIT, **window}
    )
    assert once.status_code == 201


# ---- the author on the change ----------------------------------------------------------------


def _audit(db, audit_id: str, **kw):
    row = ea.parse(
        _entry(audit_id, when=kw.pop("when", datetime.now(UTC) - timedelta(minutes=10)), **kw)
    )
    db.add(row)
    return row


def _joined(db):
    from app.db.models import AccessChange

    return db.query(AccessChange).filter_by(kind="member_added", principal_id="bob-id").one()


def test_a_member_added_gets_its_author_and_time_from_the_audit_log(app_with_sqlite: TestClient):
    from app.core.changes import rebuild_changes, record_changes_for_scan
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _audit(db, "old", when=datetime.now(UTC) - timedelta(minutes=40))
        _audit(db, "new", when=datetime.now(UTC) - timedelta(minutes=10))
        _audit(db, "failed", when=datetime.now(UTC) - timedelta(minutes=5), result="failure")
        _audit(db, "other-group", group="grp-x", when=datetime.now(UTC) - timedelta(minutes=2))
        db.commit()
        record_changes_for_scan(db, b)
        joined = _joined(db)
        assert (joined.source, joined.actor_id, joined.actor_name, joined.actor_type) == (
            "entra_audit",
            "admin-id",
            "admin@example.org",
            "user",
        )
        assert joined.direct_group_name is None
        when = joined.occurred_at
        # Rebuilding the feed from the checks keeps the author: the events are on file.
        rebuild_changes(db)
        assert (_joined(db).source, _joined(db).occurred_at) == ("entra_audit", when)


def _nest(db, *scan_ids: int) -> None:
    """grp-nested sits inside grp-1 (which holds the grant) in these checks."""
    from app.db.models import GroupMembership

    for scan_id in scan_ids:
        db.add(
            GroupMembership(
                scan_id=scan_id,
                group_id="grp-1",
                member_id="grp-nested",
                member_type="Group",
                member_name="grp-nested",
            )
        )


def test_a_member_added_to_a_nested_group_names_that_group(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.session import SessionLocal

    a, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _nest(db, a, b)
        row = _audit(db, "n", group="GRP-NESTED")
        row.group_name = "grp-nested"
        db.commit()
        record_changes_for_scan(db, b)
        joined = _joined(db)
        assert (joined.source, joined.direct_group_name) == ("entra_audit", "grp-nested")


def test_a_group_nested_between_the_checks_does_not_credit_its_own_members_author(
    app_with_sqlite: TestClient,
):
    """bob was added to grp-nested, then grp-nested was nested into grp-1: the access
    came from the nesting, not from whoever added bob. He stays "first seen"."""
    from app.core.changes import record_changes_for_scan, refine_from_directory_audits
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _nest(db, b)
        _audit(db, "n", group="grp-nested")
        db.commit()
        record_changes_for_scan(db, b)
        assert _joined(db).source == "daily_check"
        assert refine_from_directory_audits(db) == 0


def test_an_entry_from_before_the_previous_check_does_not_count(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _audit(db, "stale", when=datetime.now(UTC) - timedelta(days=2))
        db.commit()
        record_changes_for_scan(db, b)
        assert (_joined(db).source, _joined(db).actor_id) == ("daily_check", None)


def test_an_entry_arriving_after_the_check_fills_in_the_author(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan, refine_from_directory_audits
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        record_changes_for_scan(db, b)
        assert _joined(db).source == "daily_check"
        _audit(db, "late", by_user=None, by_app="sync-sp")
        db.commit()
        assert refine_from_directory_audits(db) == 1
        joined = _joined(db)
        assert (joined.source, joined.actor_type, joined.actor_name) == (
            "entra_audit",
            "app",
            "MS-PIM",
        )
        assert refine_from_directory_audits(db) == 0


def test_the_changes_api_returns_who_and_how(app_with_sqlite: TestClient):
    from app.core.changes import record_changes_for_scan
    from app.db.session import SessionLocal

    _, b = _two_checks(app_with_sqlite)
    with SessionLocal() as db:
        _audit(db, "a")
        db.commit()
        record_changes_for_scan(db, b)
    items = [
        i
        for i in app_with_sqlite.get("/api/changes").json()["items"]
        if i["kind"] == "member_added"
    ]
    assert (items[0]["source"], items[0]["actor_type"], items[0]["direct_group_name"]) == (
        "entra_audit",
        "user",
        None,
    )
