"""Tests for the cached role-definitions table and endpoint."""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi.testclient import TestClient


def _seed_role(db, **overrides):
    from app.db.models import RoleDefinition

    defaults = {
        "role_definition_id": "11111111-1111-1111-1111-111111111111",
        "role_name": "Reader",
        "role_type": "BuiltInRole",
        "description": "Read-only access.",
        "permissions": [
            {
                "actions": ["*/read"],
                "notActions": [],
                "dataActions": [],
                "notDataActions": [],
            }
        ],
        "assignable_scopes": ["/"],
    }
    defaults.update(overrides)
    role = RoleDefinition(**defaults)
    db.add(role)
    db.commit()
    return role


def test_role_definition_lookup_by_guid(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_role(db)

    r = app_with_sqlite.get("/api/iam-access/role-definitions/11111111-1111-1111-1111-111111111111")
    assert r.status_code == 200
    body = r.json()
    assert body["role_name"] == "Reader"
    assert body["role_type"] == "BuiltInRole"
    assert body["permissions"][0]["actions"] == ["*/read"]


def test_role_definition_lookup_by_full_arm_path(app_with_sqlite: TestClient):
    """The endpoint should accept ARM ids and key on the trailing GUID."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_role(db)

    arm_id = (
        "/providers/Microsoft.Authorization/roleDefinitions/11111111-1111-1111-1111-111111111111"
    )
    # Path segments need URL-encoding to traverse FastAPI's path parser.
    from urllib.parse import quote

    r = app_with_sqlite.get(f"/api/iam-access/role-definitions/{quote(arm_id, safe='')}")
    assert r.status_code == 200
    assert r.json()["role_name"] == "Reader"


def test_role_assignments_include_role_risk(app_with_sqlite: TestClient):
    """RAs serialize ``role_risk`` from the cached role-definition's actions."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    risky_guid = "aaaa1111-aaaa-1111-aaaa-111111111111"
    tame_guid = "bbbb2222-bbbb-2222-bbbb-222222222222"
    risky_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{risky_guid}"
    tame_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{tame_guid}"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=risky_guid,
            role_name="Custom Owner",
            permissions=[
                {
                    "actions": ["*"],
                    "notActions": [],
                    "dataActions": [],
                    "notDataActions": [],
                }
            ],
        )
        _seed_role(
            db,
            role_definition_id=tame_guid,
            role_name="Reader",
            permissions=[
                {
                    "actions": ["*/read"],
                    "notActions": [],
                    "dataActions": [],
                    "notDataActions": [],
                }
            ],
        )
        scan = Scan(scope="/subscriptions/abc", scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/abc",
                    scope_level="subscription",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Custom Owner",
                    role_definition_id=risky_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/abc",
                    scope_level="subscription",
                    principal_id="p2",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=tame_arm,
                ),
            ]
        )
        db.commit()
        scan_id = scan.id

    rows = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/role-assignments").json()
    by_role = {r["role_name"]: r for r in rows}
    assert by_role["Custom Owner"]["role_risk"] == "high"
    assert by_role["Reader"]["role_risk"] is None


def test_role_assignments_role_risk_null_when_definition_uncached(
    app_with_sqlite: TestClient,
):
    """RAs from a scan where the role definition didn't get cached fall back to null."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan = Scan(scope="/subscriptions/x", scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope="/subscriptions/x",
                scope_level="subscription",
                principal_id="p1",
                principal_type="User",
                role_name="Owner",
                role_definition_id=(
                    "/providers/Microsoft.Authorization/roleDefinitions/"
                    "cccc3333-cccc-3333-cccc-333333333333"
                ),
            )
        )
        db.commit()
        scan_id = scan.id

    rows = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/role-assignments").json()
    assert rows[0]["role_risk"] is None


def test_role_definition_404_when_uncached(app_with_sqlite: TestClient):
    r = app_with_sqlite.get("/api/iam-access/role-definitions/deadbeef-dead-beef-dead-beefdeadbeef")
    assert r.status_code == 404
    assert "not cached" in r.json()["detail"]


def test_role_definition_detail_includes_risk_reasons(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id="ee100000-0000-0000-0000-000000000001",
            role_name="Wildcard Writer",
            permissions=[
                {
                    "actions": [
                        "*",
                        "Microsoft.Authorization/roleAssignments/write",
                    ],
                    "notActions": [],
                    "dataActions": [],
                    "notDataActions": [],
                }
            ],
        )

    body = app_with_sqlite.get(
        "/api/iam-access/role-definitions/ee100000-0000-0000-0000-000000000001"
    ).json()
    assert body["role_risk"] == "high"
    assert any("`*`" in r for r in body["risk_reasons"])
    assert any("RBAC" in r for r in body["risk_reasons"])


def test_role_definitions_list_filters_and_sorts(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    reader_guid = "10000000-0000-0000-0000-000000000001"
    owner_guid = "10000000-0000-0000-0000-000000000002"
    custom_guid = "10000000-0000-0000-0000-000000000003"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=reader_guid,
            role_name="Reader",
            role_type="BuiltInRole",
            permissions=[
                {"actions": ["*/read"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        _seed_role(
            db,
            role_definition_id=owner_guid,
            role_name="Owner",
            role_type="BuiltInRole",
            permissions=[
                {"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        _seed_role(
            db,
            role_definition_id=custom_guid,
            role_name="Custom Reader",
            role_type="CustomRole",
            permissions=[
                {"actions": ["*/read"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        scan = Scan(scope="/sub/x", scope_level="subscription", status="done")
        db.add(scan)
        db.flush()
        db.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/sub/x",
                    scope_level="subscription",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/sub/x",
                    scope_level="subscription",
                    principal_id="p2",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=reader_arm,
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/sub/x",
                    scope_level="subscription",
                    principal_id="p3",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=reader_arm,
                ),
            ]
        )
        db.commit()

    # Default sort = name.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions").json()
    names = [r["role_name"] for r in rows]
    assert names == sorted(names, key=str.lower)
    by_name = {r["role_name"]: r for r in rows}
    assert by_name["Reader"]["assignment_count"] == 2
    assert by_name["Owner"]["assignment_count"] == 1
    assert by_name["Custom Reader"]["assignment_count"] == 0

    # Risk filter narrows to Owner.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?risk=high").json()
    assert [r["role_name"] for r in rows] == ["Owner"]

    # role_type filter narrows to BuiltInRole.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?role_type=CustomRole").json()
    assert [r["role_name"] for r in rows] == ["Custom Reader"]

    # unused filter surfaces the orphan custom role.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?unused=true").json()
    assert [r["role_name"] for r in rows] == ["Custom Reader"]

    # Search by description / name substring.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?q=custom").json()
    assert [r["role_name"] for r in rows] == ["Custom Reader"]

    # sort=usage puts the most-used role first.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?sort=usage").json()
    assert [r["role_name"] for r in rows] == ["Reader", "Owner", "Custom Reader"]

    # sort=risk puts high-risk roles before tame ones.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions?sort=risk").json()
    assert rows[0]["role_name"] == "Owner"


def test_role_usage_dedups_latest_scan_per_principal_scope(
    app_with_sqlite: TestClient,
):
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    owner_guid = "20000000-0000-0000-0000-000000000001"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=owner_guid,
            role_name="Owner",
            role_type="BuiltInRole",
            permissions=[
                {"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        # Two scans of the same root. Both grant Owner to p1 at the
        # root. Latest scan should be the only one counted.
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        s_old = Scan(
            scope="/sub/x",
            scope_level="subscription",
            status="done",
            created_at=now - timedelta(days=1),
        )
        s_new = Scan(scope="/sub/x", scope_level="subscription", status="done", created_at=now)
        # A different root with the same principal+scope should be a
        # separate row (we dedupe per scan-root, not globally).
        s_other = Scan(scope="/sub/y", scope_level="subscription", status="done", created_at=now)
        db.add_all([s_old, s_new, s_other])
        db.flush()
        for s in (s_old, s_new):
            db.add(
                RoleAssignment(
                    scan_id=s.id,
                    scope="/sub/x",
                    scope_level="subscription",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                )
            )
        db.add(
            RoleAssignment(
                scan_id=s_other.id,
                scope="/sub/y",
                scope_level="subscription",
                principal_id="p1",
                principal_type="User",
                role_name="Owner",
                role_definition_id=owner_arm,
            )
        )
        db.commit()

    body = app_with_sqlite.get(f"/api/iam-access/role-definitions/{owner_guid}/usage").json()
    assert body["assignment_count"] == 2  # x (latest) + y
    assert body["principal_count"] == 1
    assert body["scope_count"] == 2
    # The /sub/x assignment must be the one from s_new (most recent).
    sub_x_row = next(a for a in body["assignments"] if a["scope"] == "/sub/x")
    assert sub_x_row["scan_id"] == 2  # second scan inserted


def test_role_usage_404_for_uncached_role(app_with_sqlite: TestClient):
    r = app_with_sqlite.get(
        "/api/iam-access/role-definitions/99999999-9999-9999-9999-999999999999/usage"
    )
    assert r.status_code == 404


def test_role_usage_ignores_superseded_scans(app_with_sqlite: TestClient):
    """A grant removed in a later scan of the same root must disappear."""
    from datetime import UTC, datetime, timedelta

    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    owner_guid = "30000000-0000-0000-0000-000000000001"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=owner_guid,
            role_name="Owner",
            permissions=[
                {"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        now = datetime.now(UTC)
        old = Scan(
            scope="/sub/x",
            scope_level="subscription",
            status="done",
            created_at=now - timedelta(days=1),
        )
        new = Scan(scope="/sub/x", scope_level="subscription", status="done", created_at=now)
        db.add_all([old, new])
        db.flush()
        # Old scan saw Alice as Owner. New scan didn't see anything —
        # she's been removed.
        db.add(
            RoleAssignment(
                scan_id=old.id,
                scope="/sub/x",
                scope_level="subscription",
                principal_id="alice",
                principal_type="User",
                role_name="Owner",
                role_definition_id=owner_arm,
            )
        )
        db.commit()

    body = app_with_sqlite.get(f"/api/iam-access/role-definitions/{owner_guid}/usage").json()
    assert body["assignment_count"] == 0
    assert body["assignments"] == []

    # The list count must agree.
    rows = app_with_sqlite.get("/api/iam-access/role-definitions").json()
    assert next(r["assignment_count"] for r in rows if r["role_name"] == "Owner") == 0


def test_role_usage_dedupes_grant_seen_via_overlapping_scan_roots(
    app_with_sqlite: TestClient,
):
    """The same Alice/Owner/sub-platform grant seen by both an MG scan
    and a sub scan must be counted ONCE, not twice."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    owner_guid = "40000000-0000-0000-0000-000000000001"
    owner_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{owner_guid}"
    grant_scope = "/subscriptions/abc"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=owner_guid,
            role_name="Owner",
            permissions=[
                {"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        mg_scan = Scan(
            scope="/providers/microsoft.management/managementgroups/mg-root",
            scope_level="mgmt_group",
            status="done",
        )
        sub_scan = Scan(scope=grant_scope, scope_level="subscription", status="done")
        db.add_all([mg_scan, sub_scan])
        db.flush()
        # Both scans persist the same (principal, scope) grant.
        for s in (mg_scan, sub_scan):
            db.add(
                RoleAssignment(
                    scan_id=s.id,
                    scope=grant_scope,
                    scope_level="subscription",
                    principal_id="alice",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id=owner_arm,
                )
            )
        db.commit()

    body = app_with_sqlite.get(f"/api/iam-access/role-definitions/{owner_guid}/usage").json()
    assert body["assignment_count"] == 1
    assert body["principal_count"] == 1
    assert body["scope_count"] == 1

    rows = app_with_sqlite.get("/api/iam-access/role-definitions").json()
    assert next(r["assignment_count"] for r in rows if r["role_name"] == "Owner") == 1


def test_role_list_count_matches_usage_count_after_repeat_scans(
    app_with_sqlite: TestClient,
):
    """Scanning the same root ten times must not multiply the count by ten."""
    from datetime import UTC, datetime, timedelta

    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    reader_guid = "50000000-0000-0000-0000-000000000001"
    reader_arm = f"/providers/Microsoft.Authorization/roleDefinitions/{reader_guid}"

    with SessionLocal() as db:
        _seed_role(
            db,
            role_definition_id=reader_guid,
            role_name="Reader",
            permissions=[
                {"actions": ["*/read"], "notActions": [], "dataActions": [], "notDataActions": []}
            ],
        )
        now = datetime.now(UTC)
        for i in range(5):
            scan = Scan(
                scope="/sub/repeat",
                scope_level="subscription",
                status="done",
                created_at=now - timedelta(hours=5 - i),
            )
            db.add(scan)
            db.flush()
            db.add(
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/sub/repeat",
                    scope_level="subscription",
                    principal_id="alice",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id=reader_arm,
                )
            )
        db.commit()

    rows = app_with_sqlite.get("/api/iam-access/role-definitions").json()
    list_count = next(r["assignment_count"] for r in rows if r["role_name"] == "Reader")
    usage_count = app_with_sqlite.get(
        f"/api/iam-access/role-definitions/{reader_guid}/usage"
    ).json()["assignment_count"]
    assert list_count == 1
    assert usage_count == 1


def test_upsert_role_definitions_inserts_and_updates(app_with_sqlite: TestClient):
    """Re-running a scan with a renamed role should update, not duplicate."""
    from app.db.models import RoleDefinition
    from app.db.session import SessionLocal
    from app.tools.scan_persist import upsert_role_definitions

    guid = "22222222-2222-2222-2222-222222222222"
    info_v1 = {
        "name": "Custom Old",
        "role_type": "CustomRole",
        "description": "old",
        "permissions": [
            {"actions": ["*"], "notActions": [], "dataActions": [], "notDataActions": []}
        ],
        "assignable_scopes": ["/subscriptions/abc"],
    }

    with SessionLocal() as db:
        upsert_role_definitions(db, {guid: info_v1})
        db.commit()

    with SessionLocal() as db:
        row = db.get(RoleDefinition, guid)
        assert row is not None
        assert row.role_name == "Custom Old"

    # Second run with the role renamed and a tighter permission set:
    info_v2 = {
        **info_v1,
        "name": "Custom New",
        "permissions": [
            {
                "actions": ["*/read", "*/write"],
                "notActions": [],
                "dataActions": [],
                "notDataActions": [],
            }
        ],
    }
    with SessionLocal() as db:
        upsert_role_definitions(db, {guid: info_v2})
        db.commit()

    with SessionLocal() as db:
        row = db.get(RoleDefinition, guid)
        assert row.role_name == "Custom New"
        assert row.permissions[0]["actions"] == ["*/read", "*/write"]
        # One row, not two — same primary key.
        count = db.query(RoleDefinition).count()
        assert count == 1


def test_role_definition_guid_strips_arm_prefix():
    from app.tools.scope_ids import role_definition_guid

    bare = "11111111-1111-1111-1111-111111111111"
    arm = f"/subscriptions/abc/providers/Microsoft.Authorization/roleDefinitions/{bare.upper()}"
    assert role_definition_guid(bare) == bare
    assert role_definition_guid(arm) == bare


@pytest.fixture
def role_def_obj():
    """Mimic the Azure SDK RoleDefinition shape consumed by ``role_info``."""

    class _Perm:
        def __init__(self, actions, not_actions, data_actions, not_data_actions):
            self.actions = actions
            self.not_actions = not_actions
            self.data_actions = data_actions
            self.not_data_actions = not_data_actions

    class _Role:
        id = (
            "/providers/Microsoft.Authorization/roleDefinitions/"
            "33333333-3333-3333-3333-333333333333"
        )
        role_name = "Storage Blob Data Reader"
        role_type = "BuiltInRole"
        description = "Read access to blob data."
        permissions: ClassVar[list] = [
            _Perm(
                ["Microsoft.Storage/storageAccounts/blobServices/containers/read"],
                [],
                ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
                [],
            )
        ]
        assignable_scopes: ClassVar[list[str]] = ["/"]

    return _Role()


def test_role_info_caches_full_body_for_persistence(role_def_obj):
    """``role_info`` returns name+type but populates cache with full body."""

    class _Client:
        class role_definitions:
            @staticmethod
            def get_by_id(_arm_id):
                return role_def_obj

    from app.tools.scan_list import role_info

    cache: dict = {}
    name, role_type = role_info(_Client(), role_def_obj.id, cache)
    assert name == "Storage Blob Data Reader"
    assert role_type == "BuiltInRole"

    guid = "33333333-3333-3333-3333-333333333333"
    assert guid in cache
    assert cache[guid]["description"] == "Read access to blob data."
    assert cache[guid]["permissions"][0]["dataActions"] == [
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
    ]

    # Cache hit on second call → name returned without re-fetching.
    class _NeverCalled:
        class role_definitions:
            @staticmethod
            def get_by_id(_arm_id):
                raise AssertionError("should have hit cache")

    name2, _ = role_info(_NeverCalled(), role_def_obj.id, cache)
    assert name2 == "Storage Blob Data Reader"
