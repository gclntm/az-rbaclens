"""PIM during a snapshot: eligible assignments, active assignment types,
and never failing the snapshot. Azure's PIM APIs are faked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.tools import pim
from app.tools.pim import collect_pim as REAL_COLLECT_PIM

MG = "/providers/microsoft.management/managementgroups/mg-root"
SUB1, SUB2 = "/subscriptions/s1", "/subscriptions/s2"
OWNER = "/providers/Microsoft.Authorization/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
HIERARCHY = {
    MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},
    SUB1: {"name": "prod", "level": "subscription", "parent": MG},
    SUB2: {"name": "test", "level": "subscription", "parent": MG},
}
UNTIL = datetime(2027, 3, 1, tzinfo=UTC)


def _eligible(iid, scope, pid, ptype="User", end=UNTIL):
    return SimpleNamespace(
        id=iid,
        scope=scope,
        principal_id=pid,
        principal_type=ptype,
        role_definition_id=OWNER,
        member_type="Direct",
        condition=None,
        condition_version=None,
        start_date_time=None,
        end_date_time=end,
        status="Provisioned",
    )


def _active(ra_guid, kind="Assigned", end=None):
    return SimpleNamespace(
        origin_role_assignment_id=f"{SUB1}/providers/Microsoft.Authorization/roleAssignments/{ra_guid}",
        assignment_type=kind,
        end_date_time=end,
    )


def _fake_client(monkeypatch, *, eligible: dict, active: dict, fail: set = frozenset()):
    calls: list[str] = []

    class Ops:
        def __init__(self, data):
            self.data = data

        def list_for_scope(self, scope, filter=None):
            calls.append(scope)
            if scope in fail:
                raise PermissionError(scope)
            return self.data.get(scope, [])

    class FakeAuth:
        def __init__(self, *_a, **_k):
            self.role_eligibility_schedule_instances = Ops(eligible)
            self.role_assignment_schedule_instances = Ops(active)
            self.role_definitions = SimpleNamespace(
                get_by_id=lambda _id: SimpleNamespace(
                    role_name="Owner",
                    role_type="BuiltInRole",
                    description=None,
                    permissions=[],
                    assignable_scopes=[],
                )
            )

    monkeypatch.setattr(pim, "AuthorizationManagementClient", FakeAuth)
    monkeypatch.setattr(pim, "get_azure_transport", lambda: None)
    return calls


def test_queries_root_and_each_walked_subscription_and_dedupes(monkeypatch):
    # The same instance comes back from the MG and from a subscription.
    shared = _eligible("e1", SUB1, "ALICE")
    calls = _fake_client(
        monkeypatch,
        eligible={MG: [shared], SUB1: [shared, _eligible("e2", SUB1, "bob")]},
        active={SUB1: [_active("RA-1", "Activated", UNTIL), _active("ra-2")]},
    )
    r = REAL_COLLECT_PIM(MG, "mgmt_group", HIERARCHY, credential=None, role_info_cache={})

    assert calls.count(MG) == 2 and calls.count(SUB1) == 2 and calls.count(SUB2) == 2
    assert r.status == "read" and r.error is None
    assert [
        (e.azure_id, e.principal_id, e.scope_name, e.scope_level, e.role_name, e.end_at)
        for e in r.eligibilities
    ] == [
        ("e1", "alice", "prod", "subscription", "Owner", UNTIL),
        ("e2", "bob", "prod", "subscription", "Owner", UNTIL),
    ]
    assert r.active == {"ra-1": ("Activated", UNTIL), "ra-2": ("Assigned", None)}


def test_partial_and_total_failures_are_recorded_not_raised(monkeypatch):
    _fake_client(
        monkeypatch, eligible={SUB1: [_eligible("e1", SUB1, "alice")]}, active={}, fail={SUB2}
    )
    r = REAL_COLLECT_PIM(MG, "mgmt_group", HIERARCHY, credential=None, role_info_cache={})
    assert r.status == "read" and len(r.eligibilities) == 1
    assert r.error.startswith("PIM could not be read at 1 of 3 scopes")

    _fake_client(monkeypatch, eligible={}, active={}, fail={MG, SUB1, SUB2})
    r = REAL_COLLECT_PIM(MG, "mgmt_group", HIERARCHY, credential=None, role_info_cache={})
    assert r.status == "failed" and r.eligibilities == []


@pytest.fixture
def pending_scan(app_with_sqlite: TestClient) -> int:
    return app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-root"}).json()["id"]


def test_run_scan_stores_eligibility_and_assignment_types(pending_scan: int, monkeypatch):
    from app.db.models import RoleAssignment, RoleEligibility, Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    def collect(scope, scope_level, credential, on_progress):
        ra = RoleAssignment(
            scope=SUB1,
            scope_level="subscription",
            principal_id="carol",
            principal_name="carol@x",
            principal_type="User",
            role_name="Owner",
            role_definition_id=OWNER,
            raw={"id": f"{SUB1}/providers/Microsoft.Authorization/roleAssignments/RA-1"},
        )
        return [ra], HIERARCHY, {}, {}

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", collect)
    monkeypatch.setattr(pim, "collect_pim", REAL_COLLECT_PIM)
    _fake_client(
        monkeypatch,
        eligible={
            SUB1: [
                _eligible("e1", SUB1, "alice"),
                _eligible("e2", SUB1, "grp-ops", "Group", end=None),
            ]
        },
        active={SUB1: [_active("ra-1", "Activated", UNTIL)]},
    )
    monkeypatch.setattr(
        tool, "resolve_principals", lambda ids, _c: {"alice": "alice@x", "grp-ops": "ops-admins"}
    )
    resolved: list[list[str]] = []
    monkeypatch.setattr(
        tool, "resolve_group_memberships", lambda ids, _c: resolved.append(ids) or {"grp-ops": []}
    )

    tool.run_scan(pending_scan)

    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert (scan.status, scan.eligibility_status, scan.eligibility_error) == (
            "done",
            "read",
            None,
        )
        ra = db.query(RoleAssignment).filter_by(scan_id=pending_scan).one()
        assert ra.assignment_type == "Activated"
        assert ra.end_at.replace(tzinfo=UTC) == UNTIL
        rows = (
            db.query(RoleEligibility)
            .filter_by(scan_id=pending_scan)
            .order_by(RoleEligibility.azure_id)
            .all()
        )
        assert [(e.principal_id, e.principal_name, e.end_at is None) for e in rows] == [
            ("alice", "alice@x", False),
            ("grp-ops", "ops-admins", True),
        ]
    assert resolved == [["grp-ops"]]  # eligible groups get their members resolved too


def test_run_scan_survives_pim_exploding(pending_scan: int, monkeypatch):
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", lambda *a: ([], HIERARCHY, {}, {}))

    def boom(*_a, **_k):
        raise RuntimeError("PIM API down")

    monkeypatch.setattr(pim, "collect_pim", boom)
    tool.run_scan(pending_scan)
    with SessionLocal() as db:
        scan = db.get(Scan, pending_scan)
        assert (scan.status, scan.eligibility_status) == ("done", "failed")
        assert scan.eligibility_error == "RuntimeError: PIM API down"
