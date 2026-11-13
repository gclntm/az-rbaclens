"""GET /scopes/capabilities — identities by level, groups, child scopes with extra access."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_registry import MG, OWNER, RG, SUB_SCOPE, _ra, _seed_world

CONTRIBUTOR = "b24988ac-6180-42a0-ab88-20f7382dd24c"
MISSING_ROLE = "00000000-dead-beef-0000-000000000000"


def _world(client: TestClient) -> int:
    """_seed_world (Owner@MG sp-1, Reader@sub alice, Reader@rg grp-1 → alice, bob)
    plus Contributor@rg for alice and a role we never cached, on sub, for carol."""
    from app.db.models import RoleAssignment, RoleDefinition
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        db.add(
            RoleDefinition(
                role_definition_id=CONTRIBUTOR,
                role_name="Contributor",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*"],
                        "notActions": ["Microsoft.Authorization/*/Write"],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        contrib = _ra(
            scan_id,
            RG,
            "resource_group",
            "alice-id",
            "User",
            "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
        )
        contrib.role_definition_id = (
            f"/providers/Microsoft.Authorization/roleDefinitions/{CONTRIBUTOR}"
        )
        contrib.role_name = "Contributor"
        db.add(contrib)
        db.add(
            RoleAssignment(
                scan_id=scan_id,
                scope=SUB_SCOPE,
                scope_level="subscription",
                scope_name="prod",
                principal_id="carol-id",
                principal_name="carol@example.com",
                principal_type="User",
                role_name=None,
                role_type=None,
                role_definition_id=f"/providers/Microsoft.Authorization/roleDefinitions/{MISSING_ROLE}",
            )
        )
        db.commit()
    client.post("/api/settings/registry/rebuild")
    return scan_id


def _get(client: TestClient, scope: str) -> dict:
    r = client.get("/api/scopes/capabilities", params={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


def _by_level(d: dict) -> dict[str, list[str]]:
    return {lvl["level"]: [i["id"] for i in lvl["identities"]] for lvl in d["levels"]}


def test_subscription_levels_and_how(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    d = _get(app_with_sqlite, SUB_SCOPE)

    # Five levels always present; "unknown" only because carol's role was never cached.
    assert [lvl["level"] for lvl in d["levels"]] == [
        "access",
        "keys",
        "change",
        "data",
        "view",
        "unknown",
    ]
    assert _by_level(d) == {
        "access": ["sp-1"],
        "keys": [],
        "change": [],
        "data": [],
        "view": ["alice-id"],
        "unknown": ["carol-id"],
    }
    assert (d["identities"], d["people"], d["groups"], d["apps"]) == (3, 2, 0, 1)

    sp = d["levels"][0]["identities"][0]
    assert sp["kind"] == "app"
    [grant] = sp["grants"]
    assert (grant["how"], grant["from_scope_id"], grant["from_scope_name"]) == (
        "inherited",
        MG,
        "Root MG",
    )

    alice = d["levels"][4]["identities"][0]
    assert alice["grants"][0]["how"] == "here"


def test_inside_counts_only_extra_or_stronger_access(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    d = _get(app_with_sqlite, SUB_SCOPE)
    # On rg-app: grp-1 (not present here) and alice, whose Contributor there
    # beats her Reader here. sp-1's inherited Owner adds nothing new.
    assert d["inside_total"] == 1
    [rg] = d["inside"]
    assert (rg["scope_id"], rg["level"], rg["extra_identities"], rg["top_level"]) == (
        RG,
        "resource_group",
        2,
        "keys",
    )
    assert rg["by_level"] == {"keys": 1, "view": 1}


def test_resource_group_shows_strongest_grant_and_group_members(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    d = _get(app_with_sqlite, RG)
    levels = _by_level(d)
    assert levels["access"] == ["sp-1"]
    assert levels["keys"] == ["alice-id"]  # Contributor here beats inherited Reader
    assert levels["view"] == ["grp-1"]

    alice = d["levels"][1]["identities"][0]
    assert [(g["role_name"], g["level"], g["how"]) for g in alice["grants"]] == [
        ("Contributor", "keys", "here"),
        ("Reader", "view", "inherited"),
    ]
    assert "storage account keys" in alice["keys_for"]

    grp = d["levels"][4]["identities"][0]
    assert (grp["kind"], grp["member_count"]) == ("group", 2)
    assert (d["people_via_groups"], d["groups_unresolved"]) == (2, 0)
    assert d["inside"] == []


def test_unknown_scope_is_404(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    r = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": "/subscriptions/nope"})
    assert r.status_code == 404


def test_conditions_reach_all_three_views(app_with_sqlite: TestClient):
    """A constrained-delegation User Access Administrator stays at 'access'
    but every view says what the condition allows."""
    from app.db.models import RoleAssignment, RoleDefinition
    from app.db.session import SessionLocal
    from tests.test_conditions import COMPACT, EXPECTED_EXCEPT

    uaa = "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9"
    with SessionLocal() as db:
        scan_id = _seed_world(db)
        db.add(
            RoleDefinition(
                role_definition_id=uaa,
                role_name="User Access Administrator",
                role_type="BuiltInRole",
                permissions=[
                    {
                        "actions": ["*/read", "Microsoft.Authorization/*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            )
        )
        db.add(
            RoleAssignment(
                scan_id=scan_id,
                scope=RG,
                scope_level="resource_group",
                scope_name="rg-app",
                principal_id="bob-id",
                principal_name="bob@example.com",
                principal_type="User",
                role_name="User Access Administrator",
                role_type="BuiltInRole",
                role_definition_id=f"/providers/Microsoft.Authorization/roleDefinitions/{uaa}",
                condition=COMPACT,
                condition_version="2.0",
            )
        )
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")

    caps = _get(app_with_sqlite, RG)
    bob = next(i for lvl in caps["levels"] for i in lvl["identities"] if i["id"] == "bob-id")
    assert bob["level"] == "access"
    assert (bob["grants"][0]["condition_kind"], bob["grants"][0]["condition_text"]) == (
        "delegation",
        EXPECTED_EXCEPT,
    )

    rows = app_with_sqlite.get("/api/scopes/assignments", params={"scope": RG}).json()["rows"]
    row = next(r for r in rows if r["principal_id"] == "bob-id")
    assert row["condition_text"] == EXPECTED_EXCEPT
    assert all(r["condition_text"] is None for r in rows if r["principal_id"] != "bob-id")

    page = app_with_sqlite.get("/api/principals/bob-id").json()
    assert page["assignments"][0]["condition_kind"] == "delegation"

    # An unconditioned grant at the same level outranks the limited one.
    with SessionLocal() as db:
        db.add(_ra(scan_id, SUB_SCOPE, "subscription", "bob-id", "User", OWNER, "bob@example.com"))
        db.commit()
    app_with_sqlite.post("/api/settings/registry/rebuild")
    bob = next(
        i
        for lvl in _get(app_with_sqlite, RG)["levels"]
        for i in lvl["identities"]
        if i["id"] == "bob-id"
    )
    assert [(g["role_name"], g["condition_kind"]) for g in bob["grants"]] == [
        ("Owner", None),
        ("User Access Administrator", "delegation"),
    ]
