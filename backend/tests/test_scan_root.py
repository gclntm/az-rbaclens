"""A snapshot whose *root* scope is missing or unreadable must fail, not
finish as ``done`` with 0 assignments (snapshot #22 in the dev DB:
``not-a-real-thing-but-valid-name``). Failures below the root stay tolerated.

The Azure SDK clients are replaced with small fakes so the real discovery
walk and role-assignment loop run.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.tools import iam_access as tool
from app.tools import scan_list as lister
from app.tools import scan_walk as walk
from app.tools.scope_ids import normalize_scope

SUB = "11111111-1111-1111-1111-111111111111"


class NotFound(Exception):
    """Azure answered 404: the place is not there (or not visible)."""

    status_code = 404


class Forbidden(Exception):
    status_code = 403


class NoAnswer(Exception):
    """A network failure: no HTTP status at all."""


def _fake_sdk(
    monkeypatch,
    *,
    mgs: dict,
    ra_fail: set[str] = frozenset(),
    subs=(SUB,),
    ra_error: type[Exception] = NotFound,
    rgs: tuple[str, ...] = (),
):
    """``mgs``: {name: [children (type, name)]} — names missing from it 404."""

    class FakeMgClient:
        def __init__(self, *_a, **_k):
            self.management_groups = self

        def get(self, group_id, expand=None):
            if group_id not in mgs:
                raise NotFound(group_id)
            children = [SimpleNamespace(type=t, name=n, display_name=n) for t, n in mgs[group_id]]
            return SimpleNamespace(display_name=group_id, children=children, details=None)

    class FakeSubClient:
        def __init__(self, *_a, **_k):
            self.subscriptions = self

        def list(self):
            return [SimpleNamespace(subscription_id=s, display_name=f"sub-{s[:4]}") for s in subs]

    class FakeResClient:
        def __init__(self, *_a, **_k):
            self.resource_groups = SimpleNamespace(
                list=lambda: [SimpleNamespace(name=n, tags=None) for n in rgs]
            )
            self.resources = SimpleNamespace(list_by_resource_group=lambda _rg: [])

    class FakeAuthClient:
        def __init__(self, *_a, **_k):
            self.role_assignments = self

        def list_for_scope(self, scope):
            if normalize_scope(scope) in {normalize_scope(s) for s in ra_fail}:
                raise ra_error(scope)
            return []

    monkeypatch.setattr(lister, "ManagementGroupsAPI", FakeMgClient)
    monkeypatch.setattr(lister, "SubscriptionClient", FakeSubClient)
    monkeypatch.setattr(walk, "ResourceManagementClient", FakeResClient)
    monkeypatch.setattr(lister, "AuthorizationManagementClient", FakeAuthClient)
    monkeypatch.setattr(lister, "get_azure_transport", lambda: None)
    monkeypatch.setattr(lister, "resolve_principals", lambda *_a, **_k: {})


def _collect(scope: str, level: str):
    return lister.collect_assignments(scope, level, credential=None, on_progress=lambda *_a: None)


def test_missing_mgmt_group_root_fails(monkeypatch):
    _fake_sdk(monkeypatch, mgs={})
    with pytest.raises(walk.ScopeUnreachable, match="'not-a-real-thing' was not found"):
        _collect("/providers/Microsoft.Management/managementGroups/not-a-real-thing", "mgmt_group")


def test_unreadable_child_mgmt_group_is_still_tolerated(monkeypatch):
    mg = "/providers/Microsoft.Management/managementGroups/root"
    _fake_sdk(
        monkeypatch, mgs={"root": [("Microsoft.Management/managementGroups", "forbidden-child")]}
    )
    _rows, hierarchy, _roles, _members = _collect(mg, "mgmt_group")
    assert normalize_scope(mg) in hierarchy  # the walk landed despite the child's 404


def test_subscription_root_must_be_visible(monkeypatch):
    _fake_sdk(monkeypatch, mgs={})
    with pytest.raises(walk.ScopeUnreachable, match="not visible"):
        _collect("/subscriptions/22222222-2222-2222-2222-222222222222", "subscription")
    # Visible, in any case: fine.
    _rows, hierarchy, *_ = _collect(f"/subscriptions/{SUB.upper()}", "subscription")
    assert f"/subscriptions/{SUB}" in hierarchy


def test_resource_group_root_whose_assignments_cannot_be_listed_fails(monkeypatch):
    rg = f"/subscriptions/{SUB}/resourceGroups/rg-typo"
    _fake_sdk(monkeypatch, mgs={}, ra_fail={rg})
    with pytest.raises(walk.ScopeUnreachable, match="could not be listed"):
        _collect(rg, "resource_group")


def test_run_scan_records_the_failure(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import Scan
    from app.db.session import SessionLocal

    _fake_sdk(monkeypatch, mgs={})
    monkeypatch.setattr(tool, "get_credential", lambda: None)
    scan_id = app_with_sqlite.post(
        "/api/iam-access/scans", json={"scope": "not-a-real-thing"}
    ).json()["id"]
    tool.run_scan(scan_id)
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        assert scan.status == "failed"
        assert scan.error.startswith("ScopeUnreachable: management group 'not-a-real-thing'")
    # A failed snapshot registers no scope.
    assert app_with_sqlite.get("/api/scopes/tree").json() == []


# ---- below the root: a refusal is skipped, no answer stops the check ------------------------


def test_a_network_cut_below_the_root_stops_the_check(monkeypatch):
    sub = f"/subscriptions/{SUB}"
    _fake_sdk(
        monkeypatch,
        mgs={},
        rgs=("rg-a", "rg-b"),
        ra_fail={f"{sub}/resourceGroups/rg-b"},
        ra_error=NoAnswer,
    )
    with pytest.raises(
        walk.ScanInterrupted, match=r"rg-b \(place 3 of 3\).*did not answer \(NoAnswer"
    ):
        _collect(sub, "subscription")


def test_a_network_cut_at_the_root_is_not_reported_as_a_missing_scope(monkeypatch):
    sub = f"/subscriptions/{SUB}"
    _fake_sdk(monkeypatch, mgs={}, ra_fail={sub}, ra_error=NoAnswer)
    with pytest.raises(walk.ScanInterrupted, match="did not answer"):
        _collect(sub, "subscription")


def test_a_refused_place_is_skipped_and_flagged(monkeypatch):
    sub = f"/subscriptions/{SUB}"
    refused = normalize_scope(f"{sub}/resourceGroups/rg-b")
    _fake_sdk(monkeypatch, mgs={}, rgs=("rg-a", "rg-b"), ra_fail={refused}, ra_error=Forbidden)
    _rows, hierarchy, *_ = _collect(sub, "subscription")
    assert walk.unread_scopes(hierarchy) == [refused]
    assert "unread" not in hierarchy[normalize_scope(f"{sub}/resourceGroups/rg-a")]


def test_an_interrupted_check_saves_nothing(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    sub = f"/subscriptions/{SUB}"
    _fake_sdk(
        monkeypatch,
        mgs={},
        rgs=("rg-a",),
        ra_fail={f"{sub}/resourceGroups/rg-a"},
        ra_error=NoAnswer,
    )
    monkeypatch.setattr(tool, "get_credential", lambda: None)
    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": SUB}).json()["id"]
    tool.run_scan(scan_id)
    with SessionLocal() as db:
        scan = db.get(Scan, scan_id)
        assert scan.status == "failed" and scan.error.startswith("ScanInterrupted: could not read")
        assert db.query(RoleAssignment).filter_by(scan_id=scan_id).count() == 0
    assert app_with_sqlite.get("/api/scopes/tree").json() == []


def test_a_role_lookup_with_no_answer_stops_the_check_but_a_missing_role_does_not():
    role = "/providers/Microsoft.Authorization/roleDefinitions/11111111-2222-3333-4444-555555555555"

    def client(error: Exception):
        def get_by_id(_id):
            raise error

        return SimpleNamespace(role_definitions=SimpleNamespace(get_by_id=get_by_id))

    # A deleted custom role that is still assigned: keep the grant, without a name.
    assert lister.role_info(client(NotFound()), role, {}) == (None, None)
    assert lister.role_info(client(Forbidden()), role, {}) == (None, None)
    with pytest.raises(walk.ScanInterrupted, match="role definition 11111111-2222"):
        lister.role_info(client(NoAnswer("connection reset")), role, {})
