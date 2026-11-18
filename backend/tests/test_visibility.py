"""People who are not admins see their places and their own access, nothing else."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.findings import grant_fingerprint
from tests.test_registry import MG, OWNER, READER, RG, SUB_SCOPE, _arm
from tests.test_reviews import NOV, _open, _world


@pytest.fixture
def as_user(monkeypatch: pytest.MonkeyPatch):
    """Sign-in on, admins = members of group "admins"."""
    from app.core import security

    class Row:
        enabled = True
        tenant_id = "t"
        client_id = "c"
        allowed_group_ids = None
        admin_group_ids = "admins"

    def sign_in(oid: str, groups: list[str] | None = None) -> dict[str, str]:
        monkeypatch.setattr(
            security,
            "verify_token",
            lambda *_a, **_k: {"oid": oid, "name": oid, "groups": groups or []},
        )
        return {"Authorization": "Bearer x"}

    monkeypatch.setattr(security, "load_auth_settings", lambda _s: Row())
    return sign_in


def test_admins_see_everything(app_with_sqlite: TestClient, as_user):
    _world(app_with_sqlite)
    h = as_user("boss", ["admins"])
    assert app_with_sqlite.get("/api/dashboard/overview", headers=h).status_code == 200
    assert app_with_sqlite.get("/api/activity/events", headers=h).status_code == 200
    assert (
        app_with_sqlite.get("/api/scopes/capabilities", params={"scope": MG}, headers=h).status_code
        == 200
    )


def test_a_responsible_person_sees_their_place_and_what_applies_to_it(
    app_with_sqlite: TestClient, as_user
):
    _world(app_with_sqlite)  # Marc is responsible for the subscription
    h = as_user("marc-id")

    def get(url, **p):
        return app_with_sqlite.get(url, params=p, headers=h)

    # Admin pages and other places: refused.
    for url in (
        "/api/dashboard/overview",
        "/api/activity/events",
        "/api/jobs",
        "/api/iam-access/scans",
        "/api/responsible/suggestions",
        "/api/iam-access/role-definitions/x/usage",
    ):
        assert get(url).status_code == 403, url
    assert get("/api/scopes/capabilities", scope=MG).status_code == 403
    assert get(f"/api/scopes{MG}").status_code == 403

    # Their place and what is inside it.
    assert get("/api/scopes/capabilities", scope=SUB_SCOPE).status_code == 200
    assert get("/api/scopes/assignments", scope=RG).status_code == 200
    assert get(f"/api/scopes{RG}").status_code == 200
    tree_ids: list[str] = []

    def walk(nodes):
        for n in nodes:
            tree_ids.append(n["id"])
            walk(n["children"])

    walk(get("/api/scopes/tree").json())
    assert set(tree_ids) == {MG, SUB_SCOPE, RG}  # MG above, for context

    # Identities whose access applies there, including the Owner given on the MG above.
    people = {p["id"] for p in get("/api/principals").json()}
    assert people == {"sp-1", "alice-id", "grp-1"}
    assert get("/api/principals/sp-1").status_code == 200
    assert (
        get("/api/principals/bob-id").status_code == 403
    )  # only a member of a group, no grant of his own here
    assert get("/api/grants/" + grant_fingerprint(MG, _arm(OWNER), "SP-1")).status_code == 200
    assert (
        get("/api/principals/alice-id/activity").status_code == 403
    )  # what she did anywhere is not theirs to see
    assert get("/api/principals/marc-id/activity").status_code in (200, 404)  # their own: allowed


def test_someone_with_no_place_sees_only_their_own_access(app_with_sqlite: TestClient, as_user):
    _world(app_with_sqlite)
    h = as_user("bob-id")  # member of grp-1 (Reader on the resource group), responsible for nothing
    assert (
        app_with_sqlite.get(
            "/api/scopes/capabilities", params={"scope": SUB_SCOPE}, headers=h
        ).status_code
        == 403
    )
    assert app_with_sqlite.get("/api/principals/grp-1", headers=h).status_code == 200  # his group
    assert app_with_sqlite.get("/api/principals/alice-id", headers=h).status_code == 403
    assert (
        app_with_sqlite.get(
            "/api/grants/" + grant_fingerprint(RG, _arm(READER), "grp-1"), headers=h
        ).status_code
        == 200
    )
    assert app_with_sqlite.get("/api/changes", headers=h).json()["total"] == 0
    assert app_with_sqlite.get("/api/findings", headers=h).json()["total"] == 0


def test_reviews_are_limited_to_theirs(app_with_sqlite: TestClient, as_user):
    _world(app_with_sqlite)
    _open(NOV)
    h = as_user("marc-id")
    rounds = app_with_sqlite.get("/api/reviews", headers=h).json()
    assert [r["scope_id"] for r in rounds] == [SUB_SCOPE]
    admins_round = next(
        r["id"]
        for r in app_with_sqlite.get("/api/reviews", headers=as_user("boss", ["admins"])).json()
        if r["scope_id"] is None
    )
    h = as_user("marc-id")
    assert app_with_sqlite.get(f"/api/reviews/{admins_round}", headers=h).status_code == 403
    assert app_with_sqlite.post("/api/reviews/open", headers=h).status_code == 403


def test_someone_elses_page_shows_only_what_applies_to_my_places(
    app_with_sqlite: TestClient, as_user
):
    from app.core.registry import rebuild_registries
    from app.db.models import RoleAssignment, Scan
    from app.db.session import SessionLocal

    _world(app_with_sqlite)
    with (
        SessionLocal() as db
    ):  # alice also holds Owner on another subscription Marc is not responsible for
        scan = db.query(Scan).first()
        db.add(
            RoleAssignment(
                scan_id=scan.id,
                scope="/subscriptions/other",
                scope_level="subscription",
                scope_name="other",
                principal_id="alice-id",
                principal_name="alice@example.com",
                principal_type="User",
                role_name="Owner",
                role_type="BuiltInRole",
                role_definition_id=_arm(OWNER),
            )
        )
        db.commit()
        rebuild_registries(db)
        db.commit()
    page = app_with_sqlite.get("/api/principals/alice-id", headers=as_user("marc-id")).json()
    assert {a["scope"] for a in page["assignments"]} == {SUB_SCOPE, RG}  # not "other"
    full = app_with_sqlite.get(
        "/api/principals/alice-id", headers=as_user("boss", ["admins"])
    ).json()
    assert "/subscriptions/other" in {a["scope"] for a in full["assignments"]}


def test_freshness_and_the_role_catalogue_say_less_to_someone_who_is_not_an_admin(
    app_with_sqlite: TestClient, as_user
):
    """Plan phase 2 item 5: no tenant-wide detail through these two allowed lists."""
    from app.db.models import RoleDefinition
    from app.db.session import SessionLocal

    _world(app_with_sqlite)  # Marc is responsible for the subscription
    custom_used, custom_unseen = (
        "c0000000-0000-0000-0000-00000000000a",
        "c0000000-0000-0000-0000-00000000000b",
    )
    with SessionLocal() as db:
        db.add(
            RoleDefinition(
                role_definition_id=custom_unseen,
                role_name="tenant-secret-role",
                role_type="CustomRole",
                permissions=[{"actions": ["*/read"]}],
                assignable_scopes=[MG],
            )
        )
        db.add(
            RoleDefinition(
                role_definition_id=custom_used,
                role_name="ops-reader",
                role_type="CustomRole",
                permissions=[{"actions": ["*/read"]}],
                assignable_scopes=[MG, SUB_SCOPE],
            )
        )
        from app.db.models import RoleAssignment, Scan

        scan_id = db.query(Scan).order_by(Scan.id.desc()).first().id
        db.add(
            RoleAssignment(
                scan_id=scan_id,
                scope=SUB_SCOPE,
                scope_level="subscription",
                principal_id="ops-id",
                principal_type="User",
                role_name="ops-reader",
                role_type="CustomRole",
                role_definition_id=_arm(custom_used),
            )
        )
        db.commit()

    # as_user switches who is signed in for every request after it.
    def names(h):
        return {
            r["role_name"]
            for r in app_with_sqlite.get("/api/iam-access/role-definitions", headers=h).json()
        }

    assert {"tenant-secret-role", "ops-reader", "Owner", "Reader"} <= names(
        as_user("boss", ["admins"])
    )
    marc_names = names(as_user("marc-id"))
    assert (
        "tenant-secret-role" not in marc_names and {"ops-reader", "Owner", "Reader"} <= marc_names
    )

    def detail(rid, h):
        return app_with_sqlite.get(f"/api/iam-access/role-definitions/{rid}", headers=h)

    assert detail(custom_unseen, as_user("marc-id")).status_code == 403
    seen = detail(custom_used, as_user("marc-id")).json()
    assert seen["role_name"] == "ops-reader" and seen["assignable_scopes"] is None
    assert detail(custom_used, as_user("boss", ["admins"])).json()["assignable_scopes"] == [
        MG,
        SUB_SCOPE,
    ]
    assert detail(READER, as_user("marc-id")).status_code == 200  # built-in roles stay readable

    fresh_admin = app_with_sqlite.get("/api/freshness", headers=as_user("boss", ["admins"])).json()
    fresh_marc = app_with_sqlite.get("/api/freshness", headers=as_user("marc-id")).json()
    assert fresh_marc["warnings"] == [] and fresh_marc["last_check"] is None
    assert fresh_marc["access_as_of"] == fresh_admin["access_as_of"]


def test_only_admins_may_try_the_entra_audit_log(app_with_sqlite: TestClient, as_user):
    _world(app_with_sqlite)
    h = as_user("marc-id")
    assert app_with_sqlite.post("/api/activity/entra-audit/check", headers=h).status_code == 403
