"""Owners of the apps and groups that hold access (tools/graph_owners.py, plan "owners")."""

from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from app.tools import graph_owners as go

TENANT = "tenant-1"
G = go.GRAPH


class _Response:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code, self._body, self.headers = status, body or {}, {}

    def json(self) -> dict:
        return self._body


class _Client:
    """Answers by URL (params ignored); remembers what was asked."""

    def __init__(self, answers: dict[str, _Response]):
        self.answers, self.asked = answers, []

    def get(self, url, params=None, headers=None):
        self.asked.append(url)
        return self.answers.get(url, _Response(404))


def _owners(*owners: tuple[str, str, str]) -> _Response:
    return _Response(
        200,
        {
            "value": [
                {
                    "@odata.type": f"#microsoft.graph.{t}",
                    "id": i,
                    "displayName": n,
                    "userPrincipalName": n if t == "user" else None,
                }
                for i, t, n in owners
            ]
        },
    )


def _sp(app_id: str, kind: str = "Application", org: str = TENANT) -> _Response:
    return _Response(
        200, {"appId": app_id, "servicePrincipalType": kind, "appOwnerOrganizationId": org}
    )


def test_owners_of_apps_and_groups_are_read_by_where_they_come_from():
    client = _Client(
        {
            f"{G}/servicePrincipals/sp-deploy": _sp("app-1"),
            f"{G}/servicePrincipals/sp-deploy/owners": _owners(
                ("ADA-ID", "user", "ada@example.org")
            ),
            f"{G}/applications(appId='app-1')": _Response(200, {"id": "reg-1"}),
            f"{G}/applications/reg-1/owners": _owners(
                ("bob-id", "user", "bob@example.org"), ("sp-ci", "servicePrincipal", "sp-ci")
            ),
            f"{G}/servicePrincipals/sp-vendor": _sp("app-2", org="another-tenant"),
            f"{G}/servicePrincipals/sp-vendor/owners": _owners(),
            f"{G}/servicePrincipals/sp-refused": _Response(403),
            f"{G}/groups/grp-admins/owners": _owners(("carol-id", "user", "carol@example.org")),
        }
    )
    got = go.read(
        go._Graph(client, "t"),
        {
            "sp-deploy": (None, None),
            "sp-vendor": (None, None),
            "mi-1": ("ManagedIdentity", None),  # never asked: no owners
            "sp-refused": (None, None),
            "sp-gone": (None, None),
        },
        {"grp-admins"},
        TENANT,
    )
    by = {(o.object_id, o.via, o.owner_id): o for o in got.owners}
    assert set(by) == {
        ("sp-deploy", "service_principal", "ada-id"),
        ("sp-deploy", "registration", "bob-id"),
        ("sp-deploy", "registration", "sp-ci"),
        ("grp-admins", "group", "carol-id"),
    }
    assert by[("sp-deploy", "registration", "sp-ci")].owner_type == "ServicePrincipal"
    assert by[("grp-admins", "group", "carol-id")].object_kind == "group"
    # Read: everything Graph answered for, the gone app included; refused stays unknown.
    assert got.read == {"sp-deploy", "sp-vendor", "sp-gone", "grp-admins"}
    assert got.elsewhere == {"sp-vendor"}  # its registration is in its publisher's tenant
    assert not any("mi-1" in u for u in client.asked)


def test_graph_not_answering_fails_and_a_check_without_a_token_skips_owners():
    import pytest

    from app.tools.graph_resolver import GraphUnavailable

    with pytest.raises(GraphUnavailable):
        go.read(
            go._Graph(_Client({f"{G}/groups/g/owners": _Response(500)}), "t"), {}, {"g"}, TENANT
        )

    from app.tools.iam_access import _collect_owners

    class _NoToken:
        def get_token(self, *_):
            raise RuntimeError("no credential")

    class _Ra:
        principal_id, principal_type = "grp-1", "Group"

    assert _collect_owners(_NoToken(), [_Ra()], []) is None


def test_a_check_stores_the_owners_it_read(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import PrincipalOwner, RoleAssignment, ScanOwnerResolution
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool
    from app.tools import pim
    from tests.test_registry import HIERARCHY, MG, OWNER, SUB_SCOPE, _arm

    claims = base64.urlsafe_b64encode(json.dumps({"tid": TENANT}).encode()).decode().rstrip("=")

    class _Credential:
        def get_token(self, *_):
            return type("T", (), {"token": f"h.{claims}.s"})()

    def collect(*_a):
        rows = [
            RoleAssignment(
                scope=SUB_SCOPE,
                scope_level="subscription",
                principal_id=pid,
                principal_type=ptype,
                role_definition_id=_arm(OWNER),
                role_name="Owner",
            )
            for pid, ptype in (("sp-deploy", "ServicePrincipal"), ("grp-admins", "Group"))
        ]
        return rows, HIERARCHY, {}, {}

    asked = {}

    def owners(_cred, apps, groups, tenant):
        asked.update(apps=set(apps), groups=groups, tenant=tenant)
        return go.Owners(
            owners=[
                go.Owner("sp-deploy", "app", "registration", "bob-id", "User", "bob@example.org")
            ],
            read={"sp-deploy", "grp-admins"},
            elsewhere=set(),
        )

    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-root"}).json()["id"]
    monkeypatch.setattr(tool, "get_credential", lambda: _Credential())
    monkeypatch.setattr(tool, "collect_assignments", collect)
    monkeypatch.setattr(pim, "collect_pim", lambda *a, **k: pim.PimResult(status="read"))
    monkeypatch.setattr(go, "collect", owners)
    tool.run_scan(scan_id)
    assert asked == {"apps": {"sp-deploy"}, "groups": {"grp-admins"}, "tenant": TENANT}
    with SessionLocal() as db:
        (owner,) = db.query(PrincipalOwner).filter_by(scan_id=scan_id).all()
        assert (owner.object_id, owner.via, owner.owner_name) == (
            "sp-deploy",
            "registration",
            "bob@example.org",
        )
        assert {r.object_id for r in db.query(ScanOwnerResolution).filter_by(scan_id=scan_id)} == {
            "sp-deploy",
            "grp-admins",
        }
    assert MG  # the hierarchy's root


# ---- shown on the pages (step 2) ----------------------------------------------------------


def _owned_world(client: TestClient) -> int:
    """A check: sp-deploy is Owner of the subscription, owned by ada (who also reads it)
    through its registration and by sp-ci; grp-ops (Reader) has unknown owners."""
    from app.db.models import PrincipalOwner, ScanOwnerResolution
    from app.db.session import SessionLocal
    from tests.test_registry import OWNER, READER, SUB_SCOPE, _ra, _seed_roles, _seed_scan

    with SessionLocal() as db:
        _seed_roles(db)
        scan = _seed_scan(db)
        db.add_all(
            [
                _ra(
                    scan.id,
                    SUB_SCOPE,
                    "subscription",
                    "sp-deploy",
                    "ServicePrincipal",
                    OWNER,
                    "sp-deploy",
                ),
                _ra(
                    scan.id, SUB_SCOPE, "subscription", "ada-id", "User", READER, "ada@example.org"
                ),
                _ra(scan.id, SUB_SCOPE, "subscription", "grp-ops", "Group", READER, "grp-ops"),
                ScanOwnerResolution(scan_id=scan.id, object_id="sp-deploy", app_id="app-1"),
                PrincipalOwner(
                    scan_id=scan.id,
                    object_id="sp-deploy",
                    object_kind="app",
                    via="registration",
                    owner_id="ada-id",
                    owner_type="User",
                    owner_name="ada@example.org",
                ),
                PrincipalOwner(
                    scan_id=scan.id,
                    object_id="sp-deploy",
                    object_kind="app",
                    via="service_principal",
                    owner_id="ada-id",
                    owner_type="User",
                    owner_name="ada@example.org",
                ),
                PrincipalOwner(
                    scan_id=scan.id,
                    object_id="sp-deploy",
                    object_kind="app",
                    via="registration",
                    owner_id="sp-ci",
                    owner_type="ServicePrincipal",
                    owner_name="sp-ci",
                ),
            ]
        )
        db.commit()
        scan_id = scan.id
    assert client.post("/api/settings/registry/rebuild").status_code == 200
    return scan_id


def test_who_can_do_what_counts_the_owners_of_apps_and_groups(app_with_sqlite: TestClient):
    from tests.test_registry import SUB_SCOPE

    _owned_world(app_with_sqlite)
    caps = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": SUB_SCOPE}).json()
    rows = {i["id"]: i for lvl in caps["levels"] for i in lvl["identities"]}
    assert rows["sp-deploy"]["owner_count"] == 2  # ada once, though she owns it two ways; sp-ci
    assert rows["grp-ops"]["owner_count"] is None  # no check read them: unknown
    assert rows["ada-id"]["owner_count"] is None  # people have no owners


def test_an_apps_page_lists_its_owners_and_an_owners_page_what_they_own(
    app_with_sqlite: TestClient,
):
    _owned_world(app_with_sqlite)
    app_page = app_with_sqlite.get("/api/principals/sp-deploy").json()
    assert {(o["owner_id"], tuple(o["via"])) for o in app_page["owners"]} == {
        ("ada-id", ("registration", "service_principal")),
        ("sp-ci", ("registration",)),
    }
    ada = app_with_sqlite.get("/api/principals/ada-id").json()
    assert [(o["object_id"], o["object_kind"], o["level"]) for o in ada["owns"]] == [
        ("sp-deploy", "app", "access")
    ]
    assert ada["owners"] is None  # a person has no owners
    assert app_with_sqlite.get("/api/principals/grp-ops").json()["owners"] is None  # unknown


def test_an_apps_grant_page_names_its_owners(app_with_sqlite: TestClient):
    from app.core.findings import grant_fingerprint
    from tests.test_registry import OWNER, SUB_SCOPE, _arm

    _owned_world(app_with_sqlite)
    grant = app_with_sqlite.get(
        f"/api/grants/{grant_fingerprint(SUB_SCOPE, _arm(OWNER), 'sp-deploy')}"
    ).json()
    assert sorted(o["owner_id"] for o in grant["owners"]) == ["ada-id", "sp-ci"]


def _findings(scan_id: int, break_glass: list[str] | None = None) -> dict[tuple[str, str], object]:
    from app.core.advice import load_settings, save_settings
    from app.core.findings import compute_scan_findings
    from app.db.models import Finding
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        if break_glass is not None:
            save_settings(db, load_settings(db).model_copy(update={"break_glass": break_glass}))
            db.commit()
        compute_scan_findings(db, scan_id)
        return {
            (f.category, f.evidence.get("object_kind") or ""): f
            for f in db.query(Finding).filter(Finding.scan_id == scan_id).all()
            if f.category in ("app_owners", "app_owned_by_app", "group_owners", "guest_owner")
        }


def test_owners_of_an_app_with_powerful_access_need_a_look(app_with_sqlite: TestClient):
    scan_id = _owned_world(app_with_sqlite)
    got = _findings(scan_id)
    # sp-deploy is Owner of a subscription: its owners can use that; grp-ops' are unknown.
    assert set(got) == {("app_owners", "app"), ("app_owned_by_app", "app")}
    people, apps = got[("app_owners", "app")], got[("app_owned_by_app", "app")]
    assert (people.severity, apps.severity) == ("high", "high")
    assert [(o["id"], o["via"]) for o in people.evidence["owners"]] == [
        ("ada-id", ["registration", "service_principal"])
    ]
    assert [o["id"] for o in apps.evidence["owners"]] == ["sp-ci"]
    assert people.evidence["app_id"] == "app-1"
    # A break-glass identity is meant to be there: not counted as an owner.
    assert set(_findings(scan_id, break_glass=["ada-id"])) == {("app_owned_by_app", "app")}


def test_a_guest_owner_and_a_group_owner_need_a_look_only_on_powerful_grants(
    app_with_sqlite: TestClient,
):
    from app.db.models import PrincipalOwner, ScanOwnerResolution
    from app.db.session import SessionLocal
    from tests.test_registry import OWNER, READER, RG, SUB_SCOPE, _ra

    scan_id = _owned_world(app_with_sqlite)
    with SessionLocal() as db:
        db.add_all(
            [
                _ra(scan_id, RG, "resource_group", "grp-admins", "Group", OWNER, "grp-admins"),
                _ra(scan_id, SUB_SCOPE, "subscription", "grp-read", "Group", READER, "grp-read"),
                ScanOwnerResolution(scan_id=scan_id, object_id="grp-admins"),
                ScanOwnerResolution(scan_id=scan_id, object_id="grp-read"),
                PrincipalOwner(
                    scan_id=scan_id,
                    object_id="grp-admins",
                    object_kind="group",
                    via="group",
                    owner_id="guest-id",
                    owner_type="User",
                    owner_name="eve_partner.com#EXT#@tenant.onmicrosoft.com",
                ),
                PrincipalOwner(
                    scan_id=scan_id,
                    object_id="grp-read",
                    object_kind="group",
                    via="group",
                    owner_id="carol-id",
                    owner_type="User",
                    owner_name="carol@example.org",
                ),
            ]
        )
        db.commit()
    got = _findings(scan_id)
    assert got[("guest_owner", "group")].severity == "high"
    assert got[("group_owners", "group")].severity == "medium"  # a resource group: not broad
    assert all(f.evidence["owners"][0]["id"] != "carol-id" for f in got.values())  # Reader


def test_the_advice_on_an_owned_app_gives_the_commands_to_remove_owners():
    from types import SimpleNamespace

    from app.core import advice
    from tests.test_advice import SUB, advise, ra

    owners = [
        {
            "id": "ada-id",
            "name": "ada@example.org",
            "type": "User",
            "via": ["registration", "service_principal"],
        },
        {"id": "sp-ci", "name": "sp-ci", "type": "ServicePrincipal", "via": ["registration"]},
    ]

    def finding(category: str) -> SimpleNamespace:
        ev = {"object_kind": "app", "app_id": "app-1", "owners": owners}
        return SimpleNamespace(
            status="open",
            finding=SimpleNamespace(category=category, evidence=ev, detail=f"{category}."),
        )

    grant = ra(SUB, pid="sp-deploy", name="sp-deploy", ptype="ServicePrincipal", role="Contributor")
    a = advise(grant, "keys", [finding("app_owners"), finding("app_owned_by_app")])
    assert (a.rule, a.action, a.decision) == ("R17 owners", "fix_owners", None)
    assert a.commands == [
        "# ada@example.org",
        "az ad app owner remove --id app-1 --owner-object-ids ada-id",
        "az rest --method DELETE --url 'https://graph.microsoft.com/v1.0/servicePrincipals/sp-deploy/owners/ada-id/$ref'",
        "# sp-ci",
        "az ad app owner remove --id app-1 --owner-object-ids sp-ci",
    ]
    assert advice.RULES["R18 guest-owner"].weight == "must"
