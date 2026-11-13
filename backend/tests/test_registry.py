"""Scope / principal registries: upserts, "newer scan wins", API shapes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

MG = "/providers/microsoft.management/managementgroups/mg-root"
SUB = "11111111-1111-1111-1111-111111111111"
SUB_SCOPE = f"/subscriptions/{SUB}"
RG = f"{SUB_SCOPE}/resourcegroups/rg-app"
RES = f"{RG}/providers/microsoft.storage/storageaccounts/stapp"
OWNER = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
READER = "acdd72a7-3385-48ef-bd42-f606fba81ae7"

HIERARCHY = {
    MG: {"name": "Root MG", "level": "mgmt_group", "parent": None},
    SUB_SCOPE: {"name": "prod", "level": "subscription", "parent": MG},
    RG: {"name": "rg-app", "level": "resource_group", "parent": SUB_SCOPE},
    RES: {
        "name": "stapp",
        "level": "resource",
        "parent": RG,
        "type": "microsoft.storage/storageaccounts",
    },
}


def _arm(guid: str) -> str:
    return f"/providers/Microsoft.Authorization/roleDefinitions/{guid}"


def _seed_roles(db) -> None:
    from app.db.models import RoleDefinition

    db.add_all(
        [
            RoleDefinition(
                role_definition_id=OWNER,
                role_name="Owner",
                role_type="BuiltInRole",
                permissions=[{"actions": ["*"], "notActions": []}],
            ),
            RoleDefinition(
                role_definition_id=READER,
                role_name="Reader",
                role_type="BuiltInRole",
                permissions=[{"actions": ["*/read"], "notActions": []}],
            ),
        ]
    )


def _seed_scan(db, *, scope=MG, level="mgmt_group", hierarchy=None, offset_s=0, status="done"):
    from app.db.models import Scan

    now = datetime.now(UTC) + timedelta(seconds=offset_s)
    scan = Scan(
        scope=scope,
        scope_level=level,
        status=status,
        created_at=now,
        finished_at=now,
        hierarchy=hierarchy if hierarchy is not None else HIERARCHY,
    )
    db.add(scan)
    db.flush()
    return scan


def _ra(scan_id, scope, level, pid, ptype, role, name=None):
    from app.db.models import RoleAssignment

    return RoleAssignment(
        scan_id=scan_id,
        scope=scope,
        scope_level=level,
        scope_name={MG: "Root MG", SUB_SCOPE: "prod", RG: "rg-app"}.get(scope),
        principal_id=pid,
        principal_name=name,
        principal_type=ptype,
        role_name={OWNER: "Owner", READER: "Reader"}[role],
        role_type="BuiltInRole",
        role_definition_id=_arm(role),
    )


def _seed_world(db) -> int:
    """MG scan: Owner@MG (sp-1), Reader@sub (alice), Reader@rg (grp-1 → alice member)."""
    from app.db.models import GroupMembership, ScanGroupResolution

    _seed_roles(db)
    scan = _seed_scan(db)
    db.add_all(
        [
            _ra(scan.id, MG, "mgmt_group", "SP-1", "ServicePrincipal", OWNER, "sp-one"),
            _ra(
                scan.id, SUB_SCOPE, "subscription", "alice-id", "User", READER, "alice@example.com"
            ),
            _ra(scan.id, RG, "resource_group", "grp-1", "Group", READER, "grp-readers"),
            ScanGroupResolution(scan_id=scan.id, group_id="grp-1"),
            GroupMembership(
                scan_id=scan.id,
                group_id="grp-1",
                member_id="alice-id",
                member_type="User",
                member_name="alice@example.com",
            ),
            GroupMembership(
                scan_id=scan.id,
                group_id="grp-1",
                member_id="bob-id",
                member_type="User",
                member_name=None,
            ),
        ]
    )
    db.commit()
    return scan.id


# --------------------------------------------------------------------------------------
# Core upserts
# --------------------------------------------------------------------------------------


def test_upsert_scopes_links_parents_and_resource_type(app_with_sqlite: TestClient):
    from app.core.registry import upsert_scopes_from_scan
    from app.db.models import Scan, Scope
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        scan = db.get(Scan, scan_id)
        assert upsert_scopes_from_scan(db, scan) == 4
        db.commit()

        rg = db.get(Scope, RG)
        assert rg.parent_id == SUB_SCOPE and rg.name == "rg-app" and rg.level == "resource_group"
        assert rg.latest_scan_id == scan_id
        res = db.get(Scope, RES)
        assert res.resource_type == "microsoft.storage/storageaccounts" and res.parent_id == RG
        assert db.get(Scope, MG).parent_id is None


def test_newer_scan_wins_latest_pointer_but_older_does_not_regress(app_with_sqlite: TestClient):
    from app.core.registry import upsert_scopes_from_scan
    from app.db.models import Scan, Scope
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_roles(db)
        first = _seed_scan(db, offset_s=0)
        second = _seed_scan(
            db,
            scope=SUB_SCOPE,
            level="subscription",
            offset_s=10,
            hierarchy={
                SUB_SCOPE: {"name": "prod-renamed", "level": "subscription", "parent": None},
                RG: {"name": "rg-app", "level": "resource_group", "parent": SUB_SCOPE},
            },
        )
        db.commit()
        # Apply the *newer* scan first, then the older one.
        upsert_scopes_from_scan(db, db.get(Scan, second.id))
        upsert_scopes_from_scan(db, db.get(Scan, first.id))
        db.commit()

        sub = db.get(Scope, SUB_SCOPE)
        assert sub.latest_scan_id == second.id
        assert sub.name == "prod-renamed"  # older scan's name did not overwrite
        # MG-only node only seen by the older scan → owned by it.
        assert db.get(Scope, MG).latest_scan_id == first.id
        # The newer sub-rooted scan knows no parent for its root; the
        # older MG scan does. Parent links are structural, so the older
        # scan may still set it and the newer one never clears it.
        assert sub.parent_id == MG


def test_upsert_principals_coalesces_names_and_lowercases(app_with_sqlite: TestClient):
    from app.core.registry import upsert_principals_from_scan
    from app.db.models import Principal, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        assert upsert_principals_from_scan(db, db.get(Scan, scan_id)) == 4
        db.commit()

        sp = db.get(Principal, "sp-1")  # lowercased from "SP-1"
        assert sp is not None and sp.display_name == "sp-one" and sp.resolved_at is not None
        bob = db.get(Principal, "bob-id")
        assert bob.display_name is None and bob.resolved_at is None and bob.principal_type == "User"
        alice = db.get(Principal, "alice-id")
        assert alice.display_name == "alice@example.com"

        # A later scan that fails to resolve bob must not blank an
        # existing name; one that resolves him must set it.
        later = _seed_scan(db, offset_s=5)
        db.add(_ra(later.id, SUB_SCOPE, "subscription", "alice-id", "User", READER, None))
        db.add(
            _ra(later.id, SUB_SCOPE, "subscription", "bob-id", "User", READER, "bob@example.com")
        )
        db.commit()
        upsert_principals_from_scan(db, db.get(Scan, later.id))
        db.commit()
        assert db.get(Principal, "alice-id").display_name == "alice@example.com"
        assert db.get(Principal, "bob-id").display_name == "bob@example.com"


def test_record_principal_activity_only_advances(app_with_sqlite: TestClient):
    from app.core.registry import record_principal_activity, upsert_principals_from_scan
    from app.db.models import Principal, Scan
    from app.db.session import SessionLocal

    t1 = datetime(2026, 10, 1, tzinfo=UTC)
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    with SessionLocal() as db:
        scan_id = _seed_world(db)
        upsert_principals_from_scan(db, db.get(Scan, scan_id))
        assert record_principal_activity(db, {"ALICE-ID": t1}) == 1
        assert record_principal_activity(db, {"alice-id": t0}) == 0  # older: ignored
        db.commit()
        assert db.get(Principal, "alice-id").last_activity_at.replace(tzinfo=UTC) == t1


def test_rebuild_is_idempotent(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
    r1 = app_with_sqlite.post("/api/settings/registry/rebuild").json()
    r2 = app_with_sqlite.post("/api/settings/registry/rebuild").json()
    assert r1 == r2 == {"scans": 1, "scopes": 4, "principals": 4}


# --------------------------------------------------------------------------------------
# API — scopes
# --------------------------------------------------------------------------------------


def test_scope_tree_nests_and_counts_from_latest_scan(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")

    tree = app_with_sqlite.get("/api/scopes/tree").json()
    assert [n["id"] for n in tree] == [MG]
    mg = tree[0]
    assert mg["name"] == "Root MG"
    assert mg["latest_scan"]["id"] == scan_id and mg["latest_scan"]["age_days"] == 0
    assert mg["stats"] == {
        "direct_assignments": 1,
        "assignments_below": 3,
        "high_risk": 1,
        "open_findings": 1,
        "levels_below": {"access": 1, "view": 2},
    }  # Owner@MG is high-risk at broad scope → 1 open finding on the MG node
    sub = mg["children"][0]
    assert sub["id"] == SUB_SCOPE and sub["stats"]["direct_assignments"] == 1
    assert sub["stats"]["assignments_below"] == 2
    rg = sub["children"][0]
    assert rg["id"] == RG and rg["children"] == []  # resources excluded by default

    with_res = app_with_sqlite.get("/api/scopes/tree", params={"include_resources": "true"}).json()
    assert with_res[0]["children"][0]["children"][0]["children"][0]["id"] == RES

    subtree = app_with_sqlite.get("/api/scopes/tree", params={"root": SUB_SCOPE}).json()
    assert [n["id"] for n in subtree] == [SUB_SCOPE]
    assert subtree[0]["children"][0]["id"] == RG


def test_scope_detail_has_breadcrumb_and_children(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")

    # Path param with the leading slash stripped by the client, mixed case.
    r = app_with_sqlite.get(f"/api/scopes{RG.replace('rg-app', 'RG-APP')}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"]["id"] == RG
    assert [c["name"] for c in body["ancestors"]] == ["Root MG", "prod"]
    assert body["children"] == []
    full = app_with_sqlite.get(f"/api/scopes{RG}", params={"include_resources": "true"}).json()
    assert [c["id"] for c in full["children"]] == [RES]

    assert app_with_sqlite.get("/api/scopes/subscriptions/nope").status_code == 404


# --------------------------------------------------------------------------------------
# API — principals
# --------------------------------------------------------------------------------------


def test_principal_search_and_detail(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    app_with_sqlite.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")

    rows = app_with_sqlite.get("/api/principals", params={"q": "ALICE"}).json()
    assert [r["id"] for r in rows] == ["alice-id"]
    assert rows[0]["assignment_count"] == 1 and rows[0]["group_count"] == 1

    users = app_with_sqlite.get("/api/principals", params={"principal_type": "User"}).json()
    assert {r["id"] for r in users} == {"alice-id", "bob-id"}

    detail = app_with_sqlite.get("/api/principals/ALICE-ID").json()
    assert detail["principal"]["display_name"] == "alice@example.com"
    # Direct Reader@sub, then Reader@rg held by grp-1.
    assert [(a["role_name"], a["source"], a["via_group_id"]) for a in detail["assignments"]] == [
        ("Reader", "direct", None),
        ("Reader", "group", "grp-1"),
    ]
    assert detail["assignments"][0]["scope_name"] == "prod"
    assert detail["groups"] == [
        {
            "group_id": "grp-1",
            "group_name": "grp-readers",
            "scan_id": scan_id,
            "assignment_count": 1,
            "high_risk_count": 0,
        }
    ]
    assert detail["members"] is None
    assert detail["findings"] == []  # Reader@sub raises nothing

    sp = app_with_sqlite.get("/api/principals/sp-1").json()
    assert sp["assignments"][0]["role_risk"] == "high"
    assert [f["category"] for f in sp["findings"]] == ["high_risk_broad_scope"]
    assert sp["findings"][0]["status"] == "open"

    assert app_with_sqlite.get("/api/principals/ghost").status_code == 404


def test_principals_filter_by_strongest_level(app_with_sqlite: TestClient):
    """The Home tiles link here: identities whose strongest grant is at a level."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")

    every = {r["id"]: r["level"] for r in app_with_sqlite.get("/api/principals").json()}
    assert every["alice-id"] == "view" and every["sp-1"] == "access"
    assert every["bob-id"] is None  # no grant of its own

    access = app_with_sqlite.get("/api/principals", params={"level": "access"}).json()
    assert [r["id"] for r in access] == ["sp-1"]
    view = app_with_sqlite.get("/api/principals", params={"level": "view"}).json()
    assert {r["id"] for r in view} == {"alice-id", "grp-1"}
    assert app_with_sqlite.get("/api/principals", params={"level": "boss"}).status_code == 422

    kpis = app_with_sqlite.get("/api/dashboard/overview").json()["kpis"]["by_level"]
    assert kpis["access"] == len(access) and kpis["view"] == len(view)


def test_principal_counts_use_only_latest_scan(app_with_sqlite: TestClient):
    """A superseded scan's RAs must not inflate counts."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
        newer = _seed_scan(db, offset_s=30)
        db.add(_ra(newer.id, MG, "mgmt_group", "sp-1", "ServicePrincipal", OWNER, "sp-one"))
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")
    rows = {r["id"]: r for r in app_with_sqlite.get("/api/principals").json()}
    assert rows["sp-1"]["assignment_count"] == 1
    assert rows["alice-id"]["assignment_count"] == 0  # gone in the newer scan


# --------------------------------------------------------------------------------------
# Worker hook + cascade
# --------------------------------------------------------------------------------------


def test_run_scan_populates_registries(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import Principal, RoleAssignment, Scope
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool

    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": SUB}).json()["id"]
    with SessionLocal() as db:
        _seed_roles(db)
        db.commit()

    def collect(scope, scope_level, credential, on_progress):
        return (
            [
                RoleAssignment(
                    scope=scope,
                    scope_level=scope_level,
                    principal_id="NEW-SP",
                    principal_name="new-sp",
                    principal_type="ServicePrincipal",
                    role_name="Reader",
                    role_type="BuiltInRole",
                    role_definition_id=_arm(READER),
                )
            ],
            {
                scope: {"name": "prod", "level": scope_level, "parent": None},
                RG: {"name": "rg-app", "level": "resource_group", "parent": scope},
            },
            {},
            {},
        )

    monkeypatch.setattr(tool, "get_credential", lambda: object())
    monkeypatch.setattr(tool, "collect_assignments", collect)
    tool.run_scan(scan_id)

    with SessionLocal() as db:
        assert db.get(Scope, SUB_SCOPE).latest_scan_id == scan_id
        assert db.get(Scope, RG).parent_id == SUB_SCOPE
        assert db.get(Principal, "new-sp").display_name == "new-sp"

    tree = app_with_sqlite.get("/api/scopes/tree").json()
    assert tree[0]["id"] == SUB_SCOPE and tree[0]["stats"]["direct_assignments"] == 1


def test_delete_scan_nulls_latest_pointer_but_keeps_scope(app_with_sqlite: TestClient):
    from app.db.models import Scope
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    # It is the current data of its places: refused unless forced.
    refused = app_with_sqlite.delete(f"/api/iam-access/scans/{scan_id}")
    assert refused.status_code == 409 and "current data" in refused.json()["detail"]
    assert (
        app_with_sqlite.delete(
            f"/api/iam-access/scans/{scan_id}", params={"force": True}
        ).status_code
        == 204
    )
    with SessionLocal() as db:
        row = db.get(Scope, SUB_SCOPE)
        assert row is not None and row.latest_scan_id is None
    node = app_with_sqlite.get("/api/scopes/tree").json()[0]
    assert node["latest_scan"] is None and node["stats"]["direct_assignments"] == 0


def test_tree_counts_grants_below_by_capability_level(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)  # Owner@MG (access), Reader@sub (view), Reader@rg (view)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    [root] = app_with_sqlite.get("/api/scopes/tree").json()
    assert root["stats"]["levels_below"] == {"access": 1, "view": 2}
    sub = root["children"][0]
    assert sub["stats"]["levels_below"] == {"view": 2}


def test_person_page_rows_carry_the_capability_level(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
    app_with_sqlite.post("/api/settings/registry/rebuild")
    rows = app_with_sqlite.get("/api/principals/sp-1").json()["assignments"]
    assert [(r["role_name"], r["level"]) for r in rows] == [("Owner", "access")]


def test_microsoft_apps_are_recognised_from_the_check(app_with_sqlite: TestClient):
    """The check stores who publishes each app on its grants; the registry and advice use it."""
    from sqlalchemy import func, update

    from app.core.current_view import CurrentView, grant_id
    from app.db.models import RoleAssignment
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        _seed_world(db)
        db.execute(
            update(RoleAssignment)
            .where(func.lower(RoleAssignment.principal_id) == "sp-1")
            .values(
                principal_app_owner="f8cdef31-a31e-4b4a-93e4-5f571e91255a",
                principal_sp_type="Application",
            )
        )
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")

    page = app_with_sqlite.get("/api/principals/sp-1").json()
    assert page["principal"]["microsoft"] is True and page["principal"]["sp_type"] == "Application"
    assert page["advice"]["actions"] == [] and page["advice"]["fine"] == 1
    assert app_with_sqlite.get("/api/principals/alice-id").json()["principal"]["microsoft"] is False
    with SessionLocal() as db:
        gid = next(grant_id(r) for r in CurrentView(db).ras if r.principal_id.lower() == "sp-1")
    assert app_with_sqlite.get(f"/api/grants/{gid}").json()["advice"]["rule"] == "R16 microsoft-app"


def test_a_refused_place_keeps_the_earlier_check_as_its_owner(app_with_sqlite: TestClient):
    """A check that got a 403 on a place doesn't know its grants: the check
    that last read it stays current there. A place never read before is still registered."""
    from app.core.registry import upsert_scopes_from_scan
    from app.db.models import Scan, Scope
    from app.db.session import SessionLocal

    new_rg = f"{SUB_SCOPE}/resourcegroups/rg-new"
    with SessionLocal() as db:
        _seed_roles(db)
        first = _seed_scan(db, offset_s=0)
        second = _seed_scan(
            db,
            offset_s=10,
            hierarchy={
                **HIERARCHY,
                RG: {**HIERARCHY[RG], "unread": True},
                new_rg: {
                    "name": "rg-new",
                    "level": "resource_group",
                    "parent": SUB_SCOPE,
                    "unread": True,
                },
            },
        )
        db.commit()
        upsert_scopes_from_scan(db, db.get(Scan, first.id))
        upsert_scopes_from_scan(db, db.get(Scan, second.id))
        db.commit()

        assert db.get(Scope, SUB_SCOPE).latest_scan_id == second.id
        assert (
            db.get(Scope, RG).latest_scan_id == first.id
        )  # refused: the earlier check still owns it
        assert db.get(Scope, new_rg).latest_scan_id == second.id
