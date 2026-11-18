"""Last sign-ins of people with powerful grants, and app secrets (plan "sign-ins and secrets")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.tools import graph_owners as go
from app.tools import graph_signins as gs
from tests.test_owners import G, _Client, _owned_world, _Response

NOW = datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- reading --------------------------------------------------------------------------------


def test_sign_ins_are_read_and_a_refusal_leaves_them_unknown():
    old, recent = NOW - timedelta(days=200), NOW - timedelta(days=2)
    client = _Client(
        {
            f"{G}/users/ada-id": _Response(
                200,
                {
                    "id": "ada-id",
                    "createdDateTime": _iso(old),
                    "signInActivity": {
                        "lastSignInDateTime": _iso(old),
                        "lastNonInteractiveSignInDateTime": _iso(recent),
                    },
                },
            ),
            f"{G}/users/new-id": _Response(200, {"id": "new-id", "createdDateTime": _iso(NOW)}),
        }
    )
    got = {
        s.principal_id: s for s in gs.read(go._Graph(client, "t"), {"ada-id", "new-id", "gone-id"})
    }
    assert set(got) == {"ada-id", "new-id"}  # gone (404): skipped
    assert got["ada-id"].last_non_interactive_at.date() == recent.date()
    assert got["new-id"].last_sign_in_at is None
    refused = _Client({f"{G}/users/ada-id": _Response(403)})
    assert gs.read(go._Graph(refused, "t"), {"ada-id"}) is None


def test_secrets_are_read_with_the_owners_dates_only():
    sp = {
        "appId": "app-1",
        "servicePrincipalType": "Application",
        "appOwnerOrganizationId": "tenant-1",
        "passwordCredentials": [],
        "keyCredentials": [
            {"keyId": "CERT-1", "startDateTime": _iso(NOW), "endDateTime": _iso(NOW)}
        ],
    }
    app = {
        "id": "reg-1",
        "passwordCredentials": [
            {
                "keyId": "SECRET-1",
                "hint": "abc",
                "startDateTime": _iso(NOW - timedelta(days=10)),
                "endDateTime": _iso(NOW + timedelta(days=1000)),
            }
        ],
    }
    client = _Client(
        {
            f"{G}/servicePrincipals/sp-1": _Response(200, sp),
            f"{G}/servicePrincipals/sp-1/owners": _Response(200, {"value": []}),
            f"{G}/applications(appId='app-1')": _Response(200, app),
            f"{G}/applications/reg-1/owners": _Response(200, {"value": []}),
        }
    )
    got = go.read(go._Graph(client, "t"), {"sp-1": (None, None)}, set(), "tenant-1")
    assert sorted((c.via, c.kind, c.key_id) for c in got.credentials) == [
        ("registration", "secret", "secret-1"),
        ("service_principal", "certificate", "cert-1"),
    ]
    assert not any(hasattr(c, "hint") for c in got.credentials)


# ---- idle Entra admins ------------------------------------------------------------------------


def _sign_in(scan_id: int, pid: str, last: datetime | None, created: datetime):
    from app.db.models import PrincipalSignIn

    return PrincipalSignIn(
        scan_id=scan_id, principal_id=pid, last_sign_in_at=last, account_created_at=created
    )


def _idle_findings(client: TestClient, b: int) -> dict[str, dict]:
    from app.core.findings import compute_scan_findings
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        compute_scan_findings(db, b)
    found = client.get("/api/findings", params={"limit": 500}).json()["findings"]
    return {f["principal_id"]: f for f in found if f["category"] == "entra_admin_idle"}


def test_an_entra_admin_who_has_not_signed_in_for_the_idle_threshold_needs_a_look(
    app_with_sqlite: TestClient,
):
    from app.db.session import SessionLocal
    from tests.test_entra_place import GA_PATH, _entra, _world

    _, b = _world(app_with_sqlite)  # ada and carol: Global Administrator, standing
    long_ago = NOW - timedelta(days=400)
    with SessionLocal() as db:
        db.add_all(
            [
                _sign_in(b, "ada-id", NOW - timedelta(days=120), long_ago),
                _sign_in(b, "carol-id", NOW - timedelta(days=3), long_ago),
                _entra(b, "dan-id", "User", GA_PATH, "dan@example.org"),
                _sign_in(b, "dan-id", None, long_ago),  # never, an old account
                _entra(b, "eve-id", "User", GA_PATH, "eve@example.org"),
                _sign_in(b, "eve-id", None, NOW - timedelta(days=5)),  # never, but new
                _entra(b, "fay-id", "User", GA_PATH, "fay@example.org"),  # not read: unknown
            ]
        )
        db.commit()
    got = _idle_findings(app_with_sqlite, b)
    assert set(got) == {"ada-id", "dan-id"}
    assert got["ada-id"]["severity"] == "high"
    assert "never signed in" in got["dan-id"]["detail"]
    from app.core.entra_roles import ENTRA_PLACE
    from app.core.findings import grant_fingerprint

    grant = app_with_sqlite.get(
        f"/api/grants/{grant_fingerprint(ENTRA_PLACE, GA_PATH, 'ada-id')}"
    ).json()
    assert grant["advice"]["rule"] == "R03 idle-admin"
    assert grant["sign_in"]["idle"] is True  # sign-in off: everyone is an admin

    # The same threshold as "not used": raised, ada is no longer idle.
    app_with_sqlite.put("/api/settings/advice", json={"idle_days": 180})
    assert set(_idle_findings(app_with_sqlite, b)) == {"dan-id"}
    # A break-glass account is meant to sit idle.
    app_with_sqlite.put("/api/settings/advice", json={"idle_days": 90, "break_glass": ["dan-id"]})
    assert set(_idle_findings(app_with_sqlite, b)) == {"ada-id"}


def test_sign_ins_are_shown_to_admins_and_the_person_only():
    from types import SimpleNamespace

    from app.api.identity_facts import sees_sign_ins

    assert sees_sign_ins(None, "ada-id")  # admin, or sign-in off
    me = SimpleNamespace(my_ids={"ada-id", "grp-1"})
    assert sees_sign_ins(me, "ADA-ID")
    assert not sees_sign_ins(me, "bob-id")


# ---- app secrets -------------------------------------------------------------------------------


def test_long_lived_and_expired_secrets_of_a_powerful_app_need_a_look(app_with_sqlite: TestClient):
    from app.core.findings import compute_scan_findings
    from app.db.models import AppCredential, Finding
    from app.db.session import SessionLocal

    scan_id = _owned_world(app_with_sqlite)  # sp-deploy: Owner of the subscription, app-1
    with SessionLocal() as db:
        for key, via, kind, start, end in (
            ("long", "registration", "secret", NOW - timedelta(days=30), NOW + timedelta(days=900)),
            (
                "old",
                "service_principal",
                "certificate",
                NOW - timedelta(days=400),
                NOW - timedelta(days=5),
            ),
            ("fine", "registration", "secret", NOW - timedelta(days=30), NOW + timedelta(days=300)),
        ):
            db.add(
                AppCredential(
                    scan_id=scan_id,
                    object_id="sp-deploy",
                    via=via,
                    kind=kind,
                    key_id=key,
                    start_at=start,
                    end_at=end,
                )
            )
        db.commit()
        compute_scan_findings(db, scan_id)
        got = {
            f.category: f
            for f in db.query(Finding).filter(Finding.scan_id == scan_id)
            if f.category.startswith("app_secret")
        }
    assert {
        c: (f.severity, [x["key_id"] for x in f.evidence["credentials"]]) for c, f in got.items()
    } == {
        "app_secret_long_lived": ("medium", ["long"]),
        "app_secret_expired": ("low", ["old"]),
    }
    page = app_with_sqlite.get("/api/principals/sp-deploy").json()
    assert sorted((c["kind"], c["expired"], c["long_lived"]) for c in page["credentials"]) == [
        ("certificate", True, False),
        ("secret", False, False),
        ("secret", False, True),
    ]


def test_the_advice_on_secrets_gives_the_commands():
    from types import SimpleNamespace

    from tests.test_advice import SUB, advise, ra

    def finding(category: str, *creds: dict) -> SimpleNamespace:
        ev = {"app_id": "app-1", "credentials": list(creds)}
        return SimpleNamespace(
            status="open",
            finding=SimpleNamespace(category=category, evidence=ev, detail=f"{category}."),
        )

    grant = ra(SUB, pid="sp-deploy", name="sp-deploy", ptype="ServicePrincipal", role="Contributor")
    long = {"via": "registration", "kind": "secret", "key_id": "k1"}
    old = {"via": "service_principal", "kind": "certificate", "key_id": "k2"}
    a = advise(grant, "keys", [finding("app_secret_long_lived", long)])
    assert (a.rule, a.action) == ("R19 long-lived-secret", "fix_credentials")
    assert a.commands == [
        "az ad app credential reset --id app-1 --append --years 1",
        "az ad app credential delete --id app-1 --key-id k1",
    ]
    a = advise(grant, "keys", [finding("app_secret_expired", old)])
    assert (a.rule, a.commands) == (
        "R20 expired-secret",
        ["az ad sp credential delete --id sp-deploy --key-id k2 --cert"],
    )


def test_a_check_stores_sign_ins_and_secrets(app_with_sqlite: TestClient, monkeypatch):
    from app.db.models import AppCredential, PrincipalSignIn, RoleAssignment
    from app.db.session import SessionLocal
    from app.tools import iam_access as tool
    from app.tools import pim
    from tests.test_owners import TENANT
    from tests.test_registry import HIERARCHY, OWNER, READER, SUB_SCOPE, _arm, _seed_roles

    with SessionLocal() as db:
        _seed_roles(db)
        db.commit()

    class _Credential:
        def get_token(self, *_):
            return type("T", (), {"token": "h.e30.s"})()

    owner_rd = {"permissions": [{"actions": ["*"], "notActions": []}]}
    reader_rd = {"permissions": [{"actions": ["*/read"], "notActions": []}]}

    def collect(*_a):
        rows = [
            RoleAssignment(
                scope=SUB_SCOPE,
                scope_level="subscription",
                principal_id=pid,
                principal_type=ptype,
                role_definition_id=_arm(role),
                role_name="x",
            )
            for pid, ptype, role in (
                ("ada-id", "User", OWNER),
                ("bob-id", "User", READER),  # can only look: not asked
                ("sp-deploy", "ServicePrincipal", OWNER),
            )
        ]
        return rows, HIERARCHY, {OWNER: owner_rd, READER: reader_rd}, {}

    asked: dict = {}

    def sign_ins(_cred, user_ids):
        asked["users"] = set(user_ids)
        return [gs.SignIn("ada-id", NOW, None, None, NOW - timedelta(days=500))]

    def owners(*_a):
        return go.Owners(
            read={"sp-deploy"},
            credentials=[go.Credential("sp-deploy", "registration", "secret", "k1", NOW, None)],
        )

    scan_id = app_with_sqlite.post("/api/iam-access/scans", json={"scope": "mg-root"}).json()["id"]
    monkeypatch.setattr(tool, "get_credential", lambda: _Credential())
    monkeypatch.setattr(tool, "collect_assignments", collect)
    monkeypatch.setattr(pim, "collect_pim", lambda *a, **k: pim.PimResult(status="read"))
    monkeypatch.setattr(go, "collect", owners)
    monkeypatch.setattr(gs, "collect", sign_ins)
    tool.run_scan(scan_id)
    assert asked["users"] == {"ada-id"}
    with SessionLocal() as db:
        assert [s.principal_id for s in db.query(PrincipalSignIn).filter_by(scan_id=scan_id)] == [
            "ada-id"
        ]
        assert [c.key_id for c in db.query(AppCredential).filter_by(scan_id=scan_id)] == ["k1"]
    assert TENANT
