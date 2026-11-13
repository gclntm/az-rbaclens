"""The Activity Log of management groups: read over REST, stored like a subscription's (tools/mgmt_group_log.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.activity_targets import MGMT_GROUPS
from app.tools import mgmt_group_log as mg

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
ROOT = "/providers/microsoft.management/managementgroups/root"
RA = f"{ROOT}/providers/Microsoft.Authorization/roleAssignments/1111"


def _item(
    event_id: str, resource: str = RA, op: str = "Microsoft.Authorization/roleAssignments/write"
) -> dict:
    """One entry as Azure's REST API returns it."""
    return {
        "eventDataId": event_id,
        "eventTimestamp": "2026-11-09T13:47:24.1670431Z",
        "operationName": {"value": op, "localizedValue": "Create role assignment"},
        "status": {"value": "Succeeded", "localizedValue": "Succeeded"},
        "resourceId": resource,
        "resourceType": {"value": "Microsoft.Authorization/roleAssignments"},
        "caller": "ada@example.org",
        "subscriptionId": "",
        "channels": "Operation",
        "claims": {"http://schemas.microsoft.com/identity/claims/objectidentifier": "ADA-OID"},
    }


class _Answer:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code, self._body = status, body or {}

    def json(self) -> dict:
        return self._body

    def text(self) -> str:
        return str(self._body)


class _Arm:
    """Stands in for the pipeline client: answers in order, remembers what was asked."""

    def __init__(self, *answers: _Answer):
        self.answers, self.asked = list(answers), []

    def send_request(self, request):
        self.asked.append(request.url)
        return self.answers.pop(0)


def test_entries_follow_azures_pages_and_ask_for_the_window():
    arm = _Arm(
        _Answer(200, {"value": [_item("a")], "nextLink": f"{mg.ARM}/next?page=2"}),
        _Answer(200, {"value": [_item("b")]}),
    )
    got = list(mg.entries(arm, "mg-prod", NOW - timedelta(hours=2), NOW))
    assert [e.event_data_id for e in got] == ["a", "b"]
    assert got[0].operation_name.value == "Microsoft.Authorization/roleAssignments/write"
    first = arm.asked[0]
    assert (
        "/managementGroups/mg-prod/providers/microsoft.insights/eventtypes/management/values"
        in first
    )
    assert (
        "eventTimestamp ge '2026-11-10T10:00:00.000Z' and eventTimestamp le '2026-11-10T12:00:00.000Z'"
        in first
    )
    assert arm.asked[1] == f"{mg.ARM}/next?page=2"


def test_a_next_page_on_another_address_is_not_followed():
    arm = _Arm(_Answer(200, {"value": [_item("a")], "nextLink": "https://evil.example/next"}))
    assert len(list(mg.entries(arm, "mg-prod", NOW - timedelta(hours=1), NOW))) == 1
    assert len(arm.asked) == 1


@pytest.mark.parametrize("status", [403, 404])
def test_a_refused_group_is_not_readable(status):
    with pytest.raises(mg.GroupNotReadable):
        list(mg.entries(_Arm(_Answer(status)), "mg-gone", NOW - timedelta(hours=1), NOW))


def test_any_other_answer_fails_and_a_bad_group_id_is_never_sent():
    with pytest.raises(RuntimeError, match="HTTP 500"):
        list(
            mg.entries(
                _Arm(_Answer(500, {"error": "boom"})), "mg-prod", NOW - timedelta(hours=1), NOW
            )
        )
    arm = _Arm()
    with pytest.raises(ValueError):
        list(mg.entries(arm, "mg/../../subscriptions", NOW - timedelta(hours=1), NOW))
    assert arm.asked == []


def _groups(db, **seen: datetime) -> None:
    from app.db.models import Scope

    for name, at in seen.items():
        db.add(
            Scope(
                id=f"/providers/microsoft.management/managementgroups/{name}",
                name=name.upper(),
                level="mgmt_group",
                first_seen_at=at,
                last_seen_at=at,
            )
        )
    db.commit()


def test_only_groups_the_newest_checks_saw_are_read(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert mg.groups_to_read(db) == []
        _groups(db, root=NOW, prod=NOW - timedelta(hours=20), gone=NOW - timedelta(days=5))
        assert mg.groups_to_read(db) == [("prod", "PROD"), ("root", "ROOT")]


def _run(monkeypatch, per_group: dict[str, list[dict] | Exception], window_hours: int = 2) -> int:
    """Run one management-groups collection against a faked Azure; returns the pull id."""
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal
    from app.tools import activity

    def entries(_arm, group, _start, _end):
        answer = per_group[group]
        if isinstance(answer, Exception):
            raise answer
        yield from (mg.EventData.from_dict(i) for i in answer)

    monkeypatch.setattr(activity, "get_credential", lambda: None)
    monkeypatch.setattr(activity, "get_azure_transport", lambda: None)
    monkeypatch.setattr(mg, "client", lambda *_a, **_k: None)
    monkeypatch.setattr(mg, "entries", entries)
    from app.core import operation_names

    monkeypatch.setattr(operation_names, "azure_fetcher", lambda *_a, **_k: lambda _provider: [])
    with SessionLocal() as db:
        pull = ActivityPull(
            subscription_id=MGMT_GROUPS,
            start_time=NOW - timedelta(hours=window_hours),
            end_time=NOW,
            status="pending",
        )
        db.add(pull)
        db.commit()
        pull_id = pull.id
    activity.run_pull(pull_id)
    return pull_id


def test_a_collection_stores_every_groups_events_once(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import ActivityEvent, ActivityPull
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _groups(db, root=NOW, prod=NOW)
    answers = {"root": [_item("a"), _item("b")], "prod": [_item("c")]}
    first = _run(monkeypatch, answers)
    second = _run(monkeypatch, answers)  # the same window again: nothing new
    with SessionLocal() as db:
        events = db.query(ActivityEvent).all()
        assert sorted(e.event_data_id for e in events) == ["a", "b", "c"]
        assert {e.subscription_id for e in events} == {MGMT_GROUPS}
        assert {e.caller_oid for e in events} == {"ada-oid"}
        one, two = db.get(ActivityPull, first), db.get(ActivityPull, second)
        assert (one.status, one.events_persisted, one.events_seen) == ("done", 3, 3)
        assert (two.status, two.events_persisted, two.events_seen) == ("done", 0, 3)


def test_a_refused_group_is_named_and_the_rest_is_collected(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _groups(db, root=NOW, gone=NOW)
    pull_id = _run(monkeypatch, {"root": [_item("a")], "gone": mg.GroupNotReadable("gone")})
    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert (pull.status, pull.events_persisted) == ("done", 1)
        assert pull.progress_message.endswith("· Not readable: GONE")
    job = next(j for j in app_with_sqlite.get("/api/jobs").json() if j["id"] == pull_id)
    assert job["target"] == MGMT_GROUPS and "Not readable: GONE" in job["progress_message"]


def test_when_no_group_can_be_read_the_collection_fails_in_plain_words(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.core.job_errors import explain
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _groups(db, root=NOW)
    pull_id = _run(monkeypatch, {"root": mg.GroupNotReadable("root")})
    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert pull.status == "failed"
        assert explain(pull.error) == "The app's identity is not allowed to read this in Azure."


def test_without_a_known_group_the_collection_says_a_check_is_needed(
    app_with_sqlite: TestClient, monkeypatch
):
    from app.db.models import ActivityPull
    from app.db.session import SessionLocal

    pull_id = _run(monkeypatch, {})
    with SessionLocal() as db:
        pull = db.get(ActivityPull, pull_id)
        assert pull.status == "failed" and "first check" in pull.error


def test_it_can_be_scheduled_and_started_by_hand(app_with_sqlite: TestClient):
    made = app_with_sqlite.post(
        "/api/schedules",
        json={"kind": "activity_pull", "target": "Management-Groups", "every_hours": 1},
    )
    assert made.status_code == 201 and made.json()["target"] == MGMT_GROUPS
    window = {
        "start": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
    }
    once = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": MGMT_GROUPS, **window}
    )
    assert once.status_code == 201 and once.json()["subscription_id"] == MGMT_GROUPS
    bad = app_with_sqlite.post(
        "/api/activity/pulls", json={"subscription_id": "management-groupz", **window}
    )
    assert bad.status_code == 422


def test_coverage_has_one_row_for_the_management_groups(app_with_sqlite: TestClient):
    from app.core.coverage import coverage
    from app.core.staleness import warnings
    from app.db.models import JobSchedule
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        assert coverage(db, NOW) == []  # no management group known: no row
        _groups(db, root=NOW)
        row = coverage(db, NOW)[0]
        assert (row.subscription_id, row.name, row.state) == (
            MGMT_GROUPS,
            "Management groups",
            "no_schedule",
        )
        db.add(
            JobSchedule(
                kind="activity_pull",
                target=MGMT_GROUPS,
                every_hours=1,
                enabled=False,
                next_run_at=NOW,
                covered_until=NOW - timedelta(days=70),
            )
        )
        db.commit()
        row = coverage(db, NOW)[0]
        assert (row.state, row.days_left) == ("at_risk", 19)
        texts = " ".join(w.text for w in warnings(db, NOW))
        assert "the management groups" in texts or "Management groups" in texts


def test_a_change_on_a_management_group_gets_its_author_from_the_log(app_with_sqlite: TestClient):
    from app.core.changes import refine_from_events
    from app.db.models import AccessChange, ActivityEvent
    from app.db.session import SessionLocal

    at = datetime.now(UTC) - timedelta(hours=3)
    with SessionLocal() as db:
        db.add(
            AccessChange(
                kind="granted",
                source="daily_check",
                scope=ROOT,
                scope_level="mgmt_group",
                level="access",
                azure_id=RA.lower(),
                principal_id="bob-id",
                subject_key="k1",
                occurred_at=at,
            )
        )
        db.add(
            AccessChange(
                kind="granted",
                source="daily_check",
                scope="/subscriptions/s1",
                scope_level="subscription",
                level="view",
                azure_id="/subscriptions/s1/x",
                principal_id="bob-id",
                subject_key="k2",
                occurred_at=at,
            )
        )
        db.add(
            ActivityEvent(
                subscription_id=MGMT_GROUPS,
                event_data_id="ev-1",
                raw={},
                operation_name="Microsoft.Authorization/roleAssignments/write",
                status="Succeeded",
                resource_id=RA,
                caller_oid="ada-oid",
                occurred_at=at - timedelta(minutes=30),
            )
        )
        db.commit()
        assert refine_from_events(db, MGMT_GROUPS) == 1
        change = db.query(AccessChange).filter_by(subject_key="k1").one()
        assert (change.source, change.actor_id) == ("activity_log", "ada-oid")


def test_an_action_on_a_management_group_counts_as_use_of_a_role_given_there(
    app_with_sqlite: TestClient,
):
    from app.core.scan_coverage import build_scan_coverage
    from app.db.models import ActivityEvent, Scan
    from app.db.session import SessionLocal
    from tests.test_findings import OWNER, _ra, _seed_roles

    at = datetime.now(UTC) - timedelta(days=2)
    with SessionLocal() as db:
        _seed_roles(db)
        scan = Scan(
            scope=ROOT,
            scope_level="mgmt_group",
            status="done",
            hierarchy={ROOT: {"name": "root", "level": "mgmt_group", "parent": None}},
        )
        db.add(scan)
        db.flush()
        db.add(_ra(scan.id, ROOT, "mgmt_group", "ada-oid", "User", OWNER))
        db.add(_ra(scan.id, ROOT, "mgmt_group", "bob-oid", "User", OWNER))
        db.add(
            ActivityEvent(
                subscription_id=MGMT_GROUPS,
                event_data_id="ev-1",
                raw={},
                caller="ada@example.org",
                operation_name="Microsoft.Authorization/roleAssignments/write",
                status="Succeeded",
                resource_id=RA,
                caller_oid="ada-oid",
                occurred_at=at,
            )
        )
        db.commit()
        result = build_scan_coverage(db, scan)
        used = {r.principal_id: r.observed_op_count for r in result.rows}
        assert used == {"ada-oid": 1, "bob-oid": 0}
        assert (result.events_considered, result.events_attributed) == (1, 1)
        assert (
            result.subscriptions_without_events == []
        )  # the management groups are not a subscription
