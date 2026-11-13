"""API-level tests for /api/iam-access.

The Azure SDK call is monkey-patched out (see conftest), so these tests exercise
the routes, persistence, and tree assembly without touching real Azure.
"""

import pytest
from fastapi.testclient import TestClient


def test_start_scan_creates_pending_row(app_with_sqlite: TestClient):
    response = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-foo"})
    assert response.status_code == 201
    body = response.json()
    # Scopes are normalized to lowercase at ingestion (Azure ARM IDs are
    # case-insensitive; mixing casings breaks our membership lookups).
    assert body["scope"] == "/providers/microsoft.management/managementgroups/mg-foo"
    assert body["scope_level"] == "mgmt_group"
    assert body["status"] == "pending"
    assert body["role_assignment_count"] == 0


def test_start_scan_accepts_subscription_guid(app_with_sqlite: TestClient):
    sub_id = "8a3f1234-1111-2222-3333-aabbccddeeff"
    response = app_with_sqlite.post("/api/iam-access/scans", json={"scope": sub_id})
    assert response.status_code == 201
    body = response.json()
    assert body["scope"] == f"/subscriptions/{sub_id}"
    assert body["scope_level"] == "subscription"


def test_start_scan_accepts_full_arm_path(app_with_sqlite: TestClient):
    rg_path = "/subscriptions/8a3f1234-1111-2222-3333-aabbccddeeff/resourceGroups/rg-prod"
    response = app_with_sqlite.post("/api/iam-access/scans", json={"scope": rg_path})
    assert response.status_code == 201
    body = response.json()
    assert body["scope"] == rg_path.lower()
    assert body["scope_level"] == "resource_group"


def test_start_scan_accepts_resource_arm_path(app_with_sqlite: TestClient):
    storage = (
        "/subscriptions/8a3f1234-1111-2222-3333-aabbccddeeff/resourceGroups/rg-prod"
        "/providers/Microsoft.Storage/storageAccounts/myacc"
    )
    response = app_with_sqlite.post("/api/iam-access/scans", json={"scope": storage})
    assert response.status_code == 201
    body = response.json()
    assert body["scope"] == storage.lower()
    assert body["scope_level"] == "resource"


def test_list_and_get_scan(app_with_sqlite: TestClient):
    created = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-bar"}).json()
    scan_id = created["id"]

    listed = app_with_sqlite.get("/api/iam-access/scans").json()
    assert any(s["id"] == scan_id for s in listed)

    fetched = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}").json()
    assert fetched["id"] == scan_id
    assert fetched["scope"].endswith("/mg-bar")
    assert fetched["scope_level"] == "mgmt_group"


def test_get_scan_404(app_with_sqlite: TestClient):
    response = app_with_sqlite.get("/api/iam-access/scans/9999")
    assert response.status_code == 404


def test_scan_read_exposes_scope_name_from_hierarchy(app_with_sqlite: TestClient):
    """Subscriptions are addressed by GUID — friendly name comes from hierarchy."""
    from app.db.models import Scan
    from app.db.session import SessionLocal

    sub_scope = "/subscriptions/8a3f1234-1111-2222-3333-aabbccddeeff"
    with SessionLocal() as db:
        scan = Scan(
            scope=sub_scope,
            scope_level="subscription",
            status="done",
            hierarchy={
                sub_scope: {"name": "platform", "level": "subscription", "parent": None},
            },
        )
        db.add(scan)
        db.commit()
        scan_id = scan.id

    fetched = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}").json()
    assert fetched["scope_name"] == "platform"

    # Scans whose hierarchy is missing (pending, or pre-hierarchy data)
    # serialize with scope_name=null, not missing.
    pending = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-pending"}).json()
    assert pending["scope_name"] is None


def test_delete_scan_cascades(app_with_sqlite: TestClient):
    scan = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-x"}).json()
    response = app_with_sqlite.delete(f"/api/iam-access/scans/{scan['id']}")
    assert response.status_code == 204
    assert app_with_sqlite.get(f"/api/iam-access/scans/{scan['id']}").status_code == 404


def test_tree_roots_at_scan_mgmt_group_and_reattributes_ancestors(
    app_with_sqlite: TestClient,
):
    """Ancestor scopes become inherited rows on the scan root, not separate nodes."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    mg_scope = "/providers/microsoft.management/managementgroups/mg-tree"
    parent_scope = "/providers/microsoft.management/managementgroups/mg-parent"
    sub_scope = "/subscriptions/sub-a"
    rg_scope = "/subscriptions/sub-a/resourcegroups/rg-1"

    with SessionLocal() as session:
        scan = Scan(
            scope=mg_scope,
            scope_level="mgmt_group",
            status="done",
            hierarchy={
                parent_scope: {"name": "Parent MG", "level": "mgmt_group", "parent": None},
                mg_scope: {"name": "Tree MG", "level": "mgmt_group", "parent": parent_scope},
                sub_scope: {"name": "Sub A", "level": "subscription", "parent": mg_scope},
                rg_scope: {
                    "name": "Sub A / rg-1",
                    "level": "resource_group",
                    "parent": sub_scope,
                },
            },
        )
        session.add(scan)
        session.flush()
        session.add_all(
            [
                # Above the scan root → should re-attribute to mg-tree node.
                RoleAssignment(
                    scan_id=scan.id,
                    scope=parent_scope,
                    scope_name="Parent MG",
                    scope_level="mgmt_group",
                    principal_id="p0",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id="rd0",
                ),
                # At the scan root → direct on mg-tree.
                RoleAssignment(
                    scan_id=scan.id,
                    scope=mg_scope,
                    scope_name="Tree MG",
                    scope_level="mgmt_group",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id="rd1",
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_name="Sub A",
                    scope_level="subscription",
                    principal_id="p2",
                    principal_type="ServicePrincipal",
                    role_name="Owner",
                    role_definition_id="rd2",
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=rg_scope,
                    scope_name="Sub A / rg-1",
                    scope_level="resource_group",
                    principal_id="p3",
                    principal_type="Group",
                    role_name="Contributor",
                    role_definition_id="rd3",
                ),
            ]
        )
        session.commit()
        scan_id = scan.id

    tree = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/tree").json()
    # Single root — the scan's mgmt_group, NOT the parent MG.
    assert len(tree) == 1
    root = tree[0]
    assert root["scope"] == mg_scope
    assert root["name"] == "Tree MG"

    # The Parent MG's RA should be attached to mg-tree (the root) but with
    # its own scope/scope_name preserved so the frontend renders it as
    # "inherited from Parent MG".
    role_definitions = {ra["role_definition_id"]: ra for ra in root["role_assignments"]}
    assert set(role_definitions) == {"rd0", "rd1"}
    assert role_definitions["rd0"]["scope"] == parent_scope
    assert role_definitions["rd0"]["scope_name"] == "Parent MG"
    assert role_definitions["rd1"]["scope"] == mg_scope

    # subscription under mg-tree
    assert len(root["children"]) == 1
    sub_node = root["children"][0]
    assert sub_node["scope_level"] == "subscription"
    # rg under subscription
    assert len(sub_node["children"]) == 1
    rg_node = sub_node["children"][0]
    assert rg_node["scope_level"] == "resource_group"
    assert rg_node["role_assignments"][0]["role_name"] == "Contributor"


def test_resource_level_scan_attributes_descendants_and_ancestors(
    app_with_sqlite: TestClient,
):
    """When the scan root IS a resource:

    - direct RAs at the resource land on the root.
    - sub-resource RAs synthesize child nodes under the root.
    - ancestor RAs (RG / sub / MG above) re-attribute to the root, where
      the frontend renders them as inherited via their original scope.
    """
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    storage = (
        "/subscriptions/sub-x/resourcegroups/rg-1/providers/microsoft.storage/storageaccounts/myacc"
    )
    container = f"{storage}/blobservices/default/containers/c1"
    rg = "/subscriptions/sub-x/resourcegroups/rg-1"
    sub = "/subscriptions/sub-x"

    with SessionLocal() as session:
        scan = Scan(
            scope=storage,
            scope_level="resource",
            status="done",
            hierarchy={
                storage: {"name": "myacc", "level": "resource", "parent": None},
            },
        )
        session.add(scan)
        session.flush()
        session.add_all(
            [
                # Direct at the storage account.
                RoleAssignment(
                    scan_id=scan.id,
                    scope=storage,
                    scope_name="myacc",
                    scope_level="resource",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Owner",
                    role_definition_id="rd-direct",
                ),
                # Sub-resource (container) — must synthesize a child node.
                RoleAssignment(
                    scan_id=scan.id,
                    scope=container,
                    scope_name="c1",
                    scope_level="resource",
                    principal_id="p2",
                    principal_type="ServicePrincipal",
                    role_name="Storage Blob Data Reader",
                    role_definition_id="rd-container",
                ),
                # Inherited from RG (above the scan root).
                RoleAssignment(
                    scan_id=scan.id,
                    scope=rg,
                    scope_name="rg-1",
                    scope_level="resource_group",
                    principal_id="p3",
                    principal_type="Group",
                    role_name="Reader",
                    role_definition_id="rd-rg",
                ),
                # Inherited from subscription.
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub,
                    scope_name="Sub X",
                    scope_level="subscription",
                    principal_id="p4",
                    principal_type="Group",
                    role_name="Contributor",
                    role_definition_id="rd-sub",
                ),
            ]
        )
        session.commit()
        scan_id = scan.id

    tree = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/tree").json()
    assert len(tree) == 1
    root = tree[0]
    assert root["scope"] == storage
    assert root["scope_level"] == "resource"

    # Direct + ancestor RAs all anchor on the resource root.
    role_defs = {ra["role_definition_id"]: ra for ra in root["role_assignments"]}
    assert set(role_defs) == {"rd-direct", "rd-rg", "rd-sub"}
    assert role_defs["rd-direct"]["scope"] == storage
    # Ancestor RAs preserve their original scope so the frontend can
    # render "↑ from <ancestor>" using each row's stored scope_name.
    assert role_defs["rd-rg"]["scope"] == rg
    assert role_defs["rd-sub"]["scope"] == sub

    # Sub-resource synthesizes as a child of the storage account.
    assert len(root["children"]) == 1
    container_node = root["children"][0]
    assert container_node["scope"] == container
    assert container_node["scope_level"] == "resource"
    assert len(container_node["role_assignments"]) == 1
    assert container_node["role_assignments"][0]["role_definition_id"] == "rd-container"


def test_tree_synthesizes_resource_nodes_under_their_rg(app_with_sqlite: TestClient):
    """Resource-level RAs anchor on a synthetic child node under the RG that
    contains them, NOT on the scan root."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    mg = "/providers/microsoft.management/managementgroups/mg-leaf"
    sub = "/subscriptions/sub-x"
    rg = "/subscriptions/sub-x/resourcegroups/rg-1"
    storage = (
        "/subscriptions/sub-x/resourcegroups/rg-1/providers/microsoft.storage/storageaccounts/myacc"
    )

    with SessionLocal() as session:
        scan = Scan(
            scope=mg,
            scope_level="mgmt_group",
            status="done",
            hierarchy={
                mg: {"name": "Leaf MG", "level": "mgmt_group", "parent": None},
                sub: {"name": "Sub X", "level": "subscription", "parent": mg},
                rg: {"name": "Sub X / rg-1", "level": "resource_group", "parent": sub},
            },
        )
        session.add(scan)
        session.flush()
        # 1 direct at the storage-account scope; that scope was NOT walked
        # in phase 1 (we don't enumerate resources), so it's missing from
        # ``scan.hierarchy``. The tree endpoint must still anchor it inside
        # the tree — under the RG, not on the scan root.
        session.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=storage,
                scope_name="Sub X / rg-1 / myacc",
                scope_level="resource",
                principal_id="p1",
                principal_type="ServicePrincipal",
                role_name="Storage Blob Data Reader",
                role_definition_id="rd-storage",
            )
        )
        session.commit()
        scan_id = scan.id

    tree = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/tree").json()
    root = tree[0]
    assert root["scope"] == mg
    # Critically: scan root must NOT have inherited the resource RA.
    assert root["role_assignments"] == []

    sub_node = root["children"][0]
    rg_node = sub_node["children"][0]
    assert rg_node["scope"] == rg
    # The resource scope appears as a synthetic child of the RG.
    assert len(rg_node["children"]) == 1
    res_node = rg_node["children"][0]
    assert res_node["scope"] == storage
    assert res_node["scope_level"] == "resource"
    assert len(res_node["role_assignments"]) == 1
    assert res_node["role_assignments"][0]["role_name"] == "Storage Blob Data Reader"


def test_tree_includes_rg_node_with_no_direct_assignments(app_with_sqlite: TestClient):
    """RG with zero direct RAs still renders so the frontend can show
    inherited cascading from sub / MG / above-root scopes."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    mg_scope = "/providers/microsoft.management/managementgroups/mg-leaf"
    sub_scope = "/subscriptions/sub-x"
    rg_scope = "/subscriptions/sub-x/resourcegroups/rg-empty"

    with SessionLocal() as session:
        scan = Scan(
            scope=mg_scope,
            scope_level="mgmt_group",
            status="done",
            hierarchy={
                mg_scope: {"name": "Leaf MG", "level": "mgmt_group", "parent": None},
                sub_scope: {"name": "Sub X", "level": "subscription", "parent": mg_scope},
                rg_scope: {
                    "name": "Sub X / rg-empty",
                    "level": "resource_group",
                    "parent": sub_scope,
                },
            },
        )
        session.add(scan)
        session.flush()
        # RAs at mg + sub only, none at rg.
        session.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope=mg_scope,
                    scope_name="Leaf MG",
                    scope_level="mgmt_group",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id="rd-mg",
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope=sub_scope,
                    scope_name="Sub X",
                    scope_level="subscription",
                    principal_id="p2",
                    principal_type="Group",
                    role_name="Owner",
                    role_definition_id="rd-sub",
                ),
            ]
        )
        session.commit()
        scan_id = scan.id

    tree = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/tree").json()
    assert len(tree) == 1
    root = tree[0]
    assert root["scope"] == mg_scope
    sub_node = root["children"][0]
    assert sub_node["scope"] == sub_scope
    rg_node = sub_node["children"][0]
    # RG node exists in the tree even with zero direct assignments — the
    # frontend cascades sub + mg RAs in as inherited.
    assert rg_node["scope"] == rg_scope
    assert rg_node["scope_level"] == "resource_group"
    assert rg_node["role_assignments"] == []


def test_tree_falls_back_when_hierarchy_missing(app_with_sqlite: TestClient):
    """Legacy scans (no hierarchy column populated) still produce a tree via path inference."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        scan = Scan(
            scope="/providers/Microsoft.Management/managementGroups/mg-legacy",
            scope_level="mgmt_group",
            status="done",
        )  # hierarchy omitted
        session.add(scan)
        session.flush()
        session.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/sub-a",
                    scope_level="subscription",
                    principal_id="p1",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id="rd1",
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/sub-a/resourceGroups/rg-1",
                    scope_level="resource_group",
                    principal_id="p2",
                    principal_type="Group",
                    role_name="Contributor",
                    role_definition_id="rd2",
                ),
            ]
        )
        session.commit()
        scan_id = scan.id

    tree = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/tree").json()
    # Without hierarchy, parent inference still nests rg under sub.
    assert len(tree) == 1
    assert tree[0]["scope_level"] == "subscription"
    assert tree[0]["children"][0]["scope_level"] == "resource_group"


def test_filters_on_role_assignments(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        scan = Scan(
            scope="/providers/Microsoft.Management/managementGroups/mg-filter",
            scope_level="mgmt_group",
            status="done",
        )
        session.add(scan)
        session.flush()
        session.add_all(
            [
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/s",
                    scope_level="subscription",
                    principal_id="p",
                    principal_type="User",
                    role_name="Reader",
                    role_definition_id="rd",
                ),
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/s",
                    scope_level="subscription",
                    principal_id="p",
                    principal_type="ServicePrincipal",
                    role_name="Owner",
                    role_definition_id="rd",
                ),
            ]
        )
        session.commit()
        scan_id = scan.id

    all_rows = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/role-assignments").json()
    assert len(all_rows) == 2

    sp_only = app_with_sqlite.get(
        f"/api/iam-access/scans/{scan_id}/role-assignments",
        params={"principal_type": "ServicePrincipal"},
    ).json()
    assert len(sp_only) == 1
    assert sp_only[0]["principal_type"] == "ServicePrincipal"


def _seed_scan(session, scope: str, ras: list[dict]):
    """Helper for the diff tests — creates a Scan + role_assignments."""
    from app.db.models import RoleAssignment, Scan

    scan = Scan(
        scope=scope,
        scope_level="mgmt_group",
        status="done",
    )
    session.add(scan)
    session.flush()
    for ra in ras:
        session.add(
            RoleAssignment(
                scan_id=scan.id,
                scope=ra.get("scope", scope),
                scope_name=ra.get("scope_name"),
                scope_level=ra.get("scope_level", "mgmt_group"),
                principal_id=ra["principal_id"],
                principal_type=ra.get("principal_type", "User"),
                role_name=ra.get("role_name", "Reader"),
                role_definition_id=ra["role_definition_id"],
            )
        )
    session.commit()
    return scan.id


def test_diff_added_removed_kept(app_with_sqlite: TestClient):
    """Standard case — two scans of the same scope, mix of changes."""
    from app.db.session import SessionLocal

    scope = "/providers/microsoft.management/managementgroups/mg-x"
    with SessionLocal() as session:
        old_id = _seed_scan(
            session,
            scope,
            [
                # Kept across both scans.
                {"principal_id": "alice", "role_definition_id": "rd-reader"},
                {"principal_id": "bob", "role_definition_id": "rd-owner"},
                # Removed in the new scan.
                {"principal_id": "carol", "role_definition_id": "rd-reader"},
            ],
        )
        new_id = _seed_scan(
            session,
            scope,
            [
                # Kept (same identity tuple as old).
                {"principal_id": "alice", "role_definition_id": "rd-reader"},
                {"principal_id": "bob", "role_definition_id": "rd-owner"},
                # New in this scan.
                {"principal_id": "dave", "role_definition_id": "rd-contributor"},
                {"principal_id": "alice", "role_definition_id": "rd-owner"},  # alice now also Owner
            ],
        )

    body = app_with_sqlite.get(f"/api/iam-access/scans/{old_id}/diff/{new_id}").json()

    assert body["old_scan"]["id"] == old_id
    assert body["new_scan"]["id"] == new_id
    assert body["kept_count"] == 2

    added_ids = {(r["principal_id"], r["role_definition_id"]) for r in body["added"]}
    assert added_ids == {("dave", "rd-contributor"), ("alice", "rd-owner")}

    removed_ids = {(r["principal_id"], r["role_definition_id"]) for r in body["removed"]}
    assert removed_ids == {("carol", "rd-reader")}


def test_diff_treats_recreated_assignment_as_kept(app_with_sqlite: TestClient):
    """Same (scope, role, principal) but different RA db ids should be ``kept``,
    not removed + added — that's the whole point of using the identity tuple."""
    from app.db.session import SessionLocal

    scope = "/providers/microsoft.management/managementgroups/mg-y"
    with SessionLocal() as session:
        old_id = _seed_scan(
            session,
            scope,
            [{"principal_id": "p1", "role_definition_id": "rd-reader"}],
        )
        new_id = _seed_scan(
            session,
            scope,
            [{"principal_id": "p1", "role_definition_id": "rd-reader"}],
        )

    body = app_with_sqlite.get(f"/api/iam-access/scans/{old_id}/diff/{new_id}").json()
    assert body["added"] == []
    assert body["removed"] == []
    assert body["kept_count"] == 1


def test_diff_rejects_different_scopes(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        a = _seed_scan(session, "/providers/microsoft.management/managementgroups/mg-a", [])
        b = _seed_scan(session, "/providers/microsoft.management/managementgroups/mg-b", [])

    response = app_with_sqlite.get(f"/api/iam-access/scans/{a}/diff/{b}")
    assert response.status_code == 400
    assert "same scope" in response.text


def test_scan_read_reports_unresolved_principals_count(app_with_sqlite: TestClient):
    """``unresolved_principals_count`` counts distinct principal_ids whose
    Graph lookup didn't fill principal_name. Duplicate rows for the same
    principal must not inflate the count."""
    from app.db.session import SessionLocal

    scope = "/providers/microsoft.management/managementgroups/mg-unres"
    with SessionLocal() as session:
        sid = _seed_scan(
            session,
            scope,
            [
                # Two rows, same principal — counts as one unresolved.
                {
                    "principal_id": "ghost-1",
                    "principal_type": "User",
                    "role_definition_id": "rd-a",
                    "role_name": "Reader",
                },
                {
                    "principal_id": "ghost-1",
                    "principal_type": "User",
                    "role_definition_id": "rd-b",
                    "role_name": "Owner",
                },
                # A second unresolved principal.
                {
                    "principal_id": "ghost-2",
                    "principal_type": "Group",
                    "role_definition_id": "rd-c",
                    "role_name": "Contributor",
                },
                # A resolved principal — not counted.
                {
                    "principal_id": "resolved-1",
                    "principal_type": "User",
                    "role_definition_id": "rd-d",
                    "role_name": "Reader",
                },
            ],
        )
        from sqlalchemy import update

        from app.db.models import RoleAssignment

        session.execute(
            update(RoleAssignment)
            .where(RoleAssignment.principal_id == "resolved-1")
            .values(principal_name="alice@example.com")
        )
        session.commit()

    body = app_with_sqlite.get(f"/api/iam-access/scans/{sid}").json()
    assert body["unresolved_principals_count"] == 2  # ghost-1 + ghost-2


def test_diff_404_when_scan_missing(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        existing = _seed_scan(session, "/providers/microsoft.management/managementgroups/mg-z", [])

    response = app_with_sqlite.get(f"/api/iam-access/scans/{existing}/diff/9999")
    assert response.status_code == 404


def test_export_csv(app_with_sqlite: TestClient):
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        scan = Scan(
            scope="/providers/Microsoft.Management/managementGroups/mg-export",
            scope_level="mgmt_group",
            status="done",
        )
        session.add(scan)
        session.flush()
        session.add(
            RoleAssignment(
                scan_id=scan.id,
                scope="/subscriptions/s",
                scope_level="subscription",
                principal_id="p1",
                principal_type="User",
                role_name="Reader",
                role_definition_id="rd",
            )
        )
        session.commit()
        scan_id = scan.id

    response = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}/export", params={"fmt": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.text
    assert "scope,scope_name,scope_level,role_name" in body
    assert "/subscriptions/s" in body
    assert "Reader" in body


def test_export_csv_respects_filters(app_with_sqlite: TestClient):
    """Filter params on /export narrow the row set; filename gets a
    -filtered- suffix to distinguish it from a full scan export."""
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        scan = Scan(
            scope="/providers/Microsoft.Management/managementGroups/mg-flt",
            scope_level="mgmt_group",
            status="done",
        )
        session.add(scan)
        session.flush()
        for principal_type, role_name in [
            ("User", "Reader"),
            ("ServicePrincipal", "Reader"),
            ("User", "Owner"),
        ]:
            session.add(
                RoleAssignment(
                    scan_id=scan.id,
                    scope="/subscriptions/s",
                    scope_level="subscription",
                    principal_id=f"{principal_type}-{role_name}",
                    principal_type=principal_type,
                    role_name=role_name,
                    role_definition_id="rd",
                )
            )
        session.commit()
        scan_id = scan.id

    # Only ServicePrincipal rows.
    response = app_with_sqlite.get(
        f"/api/iam-access/scans/{scan_id}/export",
        params={"fmt": "csv", "principal_type": "ServicePrincipal"},
    )
    assert response.status_code == 200
    assert "filtered" in response.headers["content-disposition"]
    body = response.text
    assert "ServicePrincipal-Reader" in body
    assert "User-Reader" not in body
    assert "User-Owner" not in body


def test_scan_progress_fields_in_response(app_with_sqlite: TestClient):
    """ScanRead exposes progress_total / progress_done / progress_message."""
    from app.db.models import Scan
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        scan = Scan(
            scope="/providers/Microsoft.Management/managementGroups/mg-prog",
            scope_level="mgmt_group",
            status="running",
            progress_total=10,
            progress_done=4,
            progress_message="Listing /subscriptions/sub-a",
        )
        session.add(scan)
        session.commit()
        scan_id = scan.id

    body = app_with_sqlite.get(f"/api/iam-access/scans/{scan_id}").json()
    assert body["progress_total"] == 10
    assert body["progress_done"] == 4
    assert body["progress_message"] == "Listing /subscriptions/sub-a"


def test_scope_name_helper():
    """Pure-function test of the scope_name resolver."""
    from app.tools.scope_ids import scope_display_name

    sub_map = {"sub-a": "Production", "sub-b": "Staging"}
    mg_map = {"mg-root": "Root MG", "mg-foo": "Foo Group"}

    assert (
        scope_display_name(
            "/providers/Microsoft.Management/managementGroups/mg-foo",
            "mgmt_group",
            sub_map,
            mg_map,
        )
        == "Foo Group"
    )
    assert (
        scope_display_name("/subscriptions/sub-a", "subscription", sub_map, mg_map) == "Production"
    )
    assert (
        scope_display_name(
            "/subscriptions/sub-b/resourceGroups/rg-1",
            "resource_group",
            sub_map,
            mg_map,
        )
        == "Staging / rg-1"
    )
    # Unknown subscription falls back to the ID
    assert (
        scope_display_name(
            "/subscriptions/unknown-sub/resourceGroups/rg-2",
            "resource_group",
            sub_map,
            mg_map,
        )
        == "unknown-sub / rg-2"
    )


def test_resolve_principals_returns_empty_when_token_unavailable():
    """If credential.get_token raises, resolver returns {} (no Graph call)."""
    from app.tools.graph_resolver import resolve_principals

    class FailingCred:
        def get_token(self, *_args, **_kwargs):
            raise RuntimeError("no creds")

    assert resolve_principals({"pid-1": "User", "pid-2": "Group"}, FailingCred()) == {}


def test_resolve_principals_groups_by_type_and_picks_upn_for_users(monkeypatch):
    """Resolver issues one typed batch per principal type; users → UPN."""
    import app.tools.graph_resolver as resolver_mod

    posts: list[dict] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    type_to_payload = {
        "user": {
            "value": [
                {
                    "id": "u1",
                    "@odata.type": "#microsoft.graph.user",
                    "userPrincipalName": "alice@example.com",
                    "displayName": "Alice",
                }
            ]
        },
        "group": {
            "value": [
                {
                    "id": "g1",
                    "@odata.type": "#microsoft.graph.group",
                    "displayName": "Engineering",
                }
            ]
        },
        "servicePrincipal": {
            "value": [
                {
                    "id": "sp1",
                    "@odata.type": "#microsoft.graph.servicePrincipal",
                    "displayName": "sp-terraform",
                    "appOwnerOrganizationId": "F8CDEF31-A31E-4B4A-93E4-5F571E91255A",
                    "servicePrincipalType": "Application",
                }
            ]
        },
    }

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, headers, json):
            posts.append({"url": url, "body": json})
            graph_type = json.get("types", [None])[0]
            return FakeResponse(type_to_payload.get(graph_type, {"value": []}))

    monkeypatch.setattr(resolver_mod.httpx, "Client", FakeClient)

    class FakeToken:
        token = "fake-token"

    class FakeCred:
        def get_token(self, *_args, **_kwargs):
            return FakeToken()

    apps: dict = {}
    result = resolver_mod.resolve_principals(
        {"u1": "User", "g1": "Group", "sp1": "ServicePrincipal"}, FakeCred(), apps_out=apps
    )
    # Only service principals report who publishes the app.
    assert apps == {"sp1": ("F8CDEF31-A31E-4B4A-93E4-5F571E91255A", "Application")}
    assert resolver_mod.is_microsoft_app(apps["sp1"][0])
    assert not resolver_mod.is_microsoft_app("11111111-2222-3333-4444-555555555555")
    assert not resolver_mod.is_microsoft_app(None)

    assert result == {
        "u1": "alice@example.com",
        "g1": "Engineering",
        "sp1": "sp-terraform",
    }
    # One typed call per type, each scoped to its own ids.
    types_called = sorted(p["body"]["types"][0] for p in posts)
    assert types_called == ["group", "servicePrincipal", "user"]


def test_hierarchy_parents_never_point_to_deeper_levels(app_with_sqlite: TestClient):
    """RBAC inheritance is top-down: a child's parent must be at the same or
    a higher level. mgmt_group < subscription < resource_group < resource."""
    from app.db.models import Scan
    from app.db.session import SessionLocal
    from app.tools.scope_ids import LEVEL_ORDER

    mg = "/providers/Microsoft.Management/managementGroups/mg-root"
    sub = "/subscriptions/sub-1"
    rg = "/subscriptions/sub-1/resourceGroups/rg-1"
    res = "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/x"

    hierarchy = {
        mg: {"name": "Root MG", "level": "mgmt_group", "parent": None},
        sub: {"name": "Sub 1", "level": "subscription", "parent": mg},
        rg: {"name": "Sub 1 / rg-1", "level": "resource_group", "parent": sub},
        res: {"name": "x", "level": "resource", "parent": rg},
    }

    with SessionLocal() as session:
        scan = Scan(
            scope=mg,
            scope_level="mgmt_group",
            status="done",
            hierarchy=hierarchy,
        )
        session.add(scan)
        session.commit()

    # Walk the persisted hierarchy and check every parent → child link.
    for child_scope, meta in hierarchy.items():
        parent_scope = meta["parent"]
        if parent_scope is None:
            continue
        parent_level = hierarchy[parent_scope]["level"]
        child_level = meta["level"]
        assert LEVEL_ORDER[parent_level] <= LEVEL_ORDER[child_level], (
            f"inheritance flows the wrong way: parent {parent_scope} ({parent_level}) "
            f"is below child {child_scope} ({child_level})"
        )


def test_parse_scope_input_classifies_each_level():
    """Each Azure scope shape parses to the correct level. Output paths are
    normalized to lowercase (Azure ARM IDs are case-insensitive)."""
    from app.tools.scope_ids import parse_scope_input

    # Bare names / IDs.
    assert parse_scope_input("mg-platform") == (
        "/providers/microsoft.management/managementgroups/mg-platform",
        "mgmt_group",
    )
    assert parse_scope_input("8a3f1234-1111-2222-3333-aabbccddeeff") == (
        "/subscriptions/8a3f1234-1111-2222-3333-aabbccddeeff",
        "subscription",
    )

    # Full ARM paths — case is normalized to lowercase.
    assert parse_scope_input("/providers/Microsoft.Management/managementGroups/mg-x") == (
        "/providers/microsoft.management/managementgroups/mg-x",
        "mgmt_group",
    )
    assert parse_scope_input("/subscriptions/abc") == (
        "/subscriptions/abc",
        "subscription",
    )
    assert parse_scope_input("/subscriptions/abc/resourceGroups/rg-1") == (
        "/subscriptions/abc/resourcegroups/rg-1",
        "resource_group",
    )
    assert parse_scope_input(
        "/subscriptions/abc/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/x"
    ) == (
        "/subscriptions/abc/resourcegroups/rg-1/providers/microsoft.storage/storageaccounts/x",
        "resource",
    )

    # ``<sub-guid>/<rg-name>`` shorthand.
    sub = "8a3f1234-1111-2222-3333-aabbccddeeff"
    assert parse_scope_input(f"{sub}/rg-test") == (
        f"/subscriptions/{sub}/resourcegroups/rg-test",
        "resource_group",
    )
    # ``<sub-guid>/resourceGroups/<rg-name>`` — case-insensitive segment.
    assert parse_scope_input(f"{sub}/ResourceGroups/rg-test") == (
        f"/subscriptions/{sub}/resourcegroups/rg-test",
        "resource_group",
    )
    # Non-GUID head with a slash isn't valid shorthand; should raise.
    with pytest.raises(ValueError):
        parse_scope_input("not-a-guid/rg-test")


def test_normalize_scope_lowercases_input():
    from app.tools.scope_ids import normalize_scope

    assert (
        normalize_scope("/providers/Microsoft.Management/managementGroups/MG-Foo")
        == "/providers/microsoft.management/managementgroups/mg-foo"
    )
    # Already lowercase — no-op.
    assert normalize_scope("/subscriptions/abc") == "/subscriptions/abc"


def test_level_from_scope():
    """Inherited assignments need their scope_level inferred from the path."""
    from app.tools.scope_ids import level_from_scope

    assert level_from_scope("/providers/Microsoft.Management/managementGroups/root") == "mgmt_group"
    assert level_from_scope("/subscriptions/abc") == "subscription"
    assert level_from_scope("/subscriptions/abc/resourceGroups/rg-1") == "resource_group"
    assert (
        level_from_scope(
            "/subscriptions/abc/resourceGroups/rg-1/providers/Microsoft.Storage/storageAccounts/x"
        )
        == "resource"
    )
