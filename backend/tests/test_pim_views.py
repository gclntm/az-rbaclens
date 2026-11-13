"""PIM in the read APIs: on-request placement, PIM summary, eligible rows
on the scope's Access payload and the principal page."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from tests.test_registry import MG, OWNER, RG, SUB_SCOPE, _seed_world

UNTIL = datetime(2027, 3, 1, tzinfo=UTC)
OWNER_ARM = f"/providers/Microsoft.Authorization/roleDefinitions/{OWNER}"


def _world(client: TestClient, *, status: str | None = "read", error: str | None = None) -> int:
    """_seed_world (Owner@MG sp-1, Reader@sub alice, Reader@rg grp-1) plus:
    sp-1's Owner is time-bound; alice and dave are eligible for Owner on the sub."""
    from app.db.models import RoleAssignment, RoleEligibility, Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        scan = db.get(Scan, scan_id)
        scan.eligibility_status, scan.eligibility_error = status, error
        owner = db.query(RoleAssignment).filter_by(scan_id=scan_id, principal_id="SP-1").one()
        owner.assignment_type, owner.end_at = "Activated", UNTIL
        for i, (pid, name) in enumerate(
            (("alice-id", "alice@example.com"), ("dave-id", "dave@example.com"))
        ):
            db.add(
                RoleEligibility(
                    scan_id=scan_id,
                    azure_id=f"e{i}",
                    scope=SUB_SCOPE,
                    scope_name="prod",
                    scope_level="subscription",
                    principal_id=pid,
                    principal_name=name,
                    principal_type="User",
                    role_definition_id=OWNER_ARM,
                    role_name="Owner",
                    role_type="BuiltInRole",
                    member_type="Direct",
                    end_at=UNTIL if pid == "dave-id" else None,
                )
            )
        db.commit()
    client.post("/api/settings/registry/rebuild")
    return scan_id


def _caps(client: TestClient, scope: str) -> dict:
    r = client.get("/api/scopes/capabilities", params={"scope": scope})
    assert r.status_code == 200, r.text
    return r.json()


def _ident(d: dict, pid: str) -> dict:
    return next(i for lvl in d["levels"] for i in lvl["identities"] if i["id"] == pid)


def test_eligible_identities_sit_at_their_level_on_request(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    d = _caps(app_with_sqlite, SUB_SCOPE)
    access = d["levels"][0]
    assert access["level"] == "access"
    assert sorted(i["id"] for i in access["identities"]) == ["alice-id", "dave-id", "sp-1"]
    assert access["on_request"] == 2

    alice = _ident(d, "alice-id")  # Reader active, Owner on request
    assert alice["on_request"] and [g["role_name"] for g in alice["grants"]] == ["Reader"]
    [eg] = alice["eligible_grants"]
    assert (eg["role_name"], eg["eligible"], eg["assignment_id"], eg["until"]) == (
        "Owner",
        True,
        None,
        None,
    )

    dave = _ident(d, "dave-id")  # only eligible
    assert dave["on_request"] and dave["grants"] == []
    assert dave["eligible_grants"][0]["until"].startswith("2027-03-01")

    sp = _ident(d, "sp-1")  # time-bound, activated through PIM
    assert not sp["on_request"]
    assert (sp["grants"][0]["activated"], sp["grants"][0]["until"][:10]) == (True, "2027-03-01")

    assert d["pim"] == {
        "status": "read",
        "error": None,
        "unchecked": [],
        "eligible_identities": 2,
        "time_bound_grants": 1,
        "permanent_privileged": 0,
    }


def test_pim_status_reports_unchecked_and_failed_snapshots(app_with_sqlite: TestClient):
    scan_id = _world(app_with_sqlite, status=None)
    pim = _caps(app_with_sqlite, SUB_SCOPE)["pim"]
    assert pim["status"] == "not_checked"
    assert pim["unchecked"] == [{"id": scan_id, "scope_id": MG, "name": "Root MG"}]


def test_pim_failed_status_carries_the_error(app_with_sqlite: TestClient):
    _world(app_with_sqlite, status="failed", error="PIM could not be read: …")
    pim = _caps(app_with_sqlite, SUB_SCOPE)["pim"]
    assert (pim["status"], pim["error"]) == ("failed", "PIM could not be read: …")


def test_access_payload_lists_eligibility_by_source(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    sub = app_with_sqlite.get("/api/scopes/assignments", params={"scope": SUB_SCOPE}).json()
    assert [(r["principal_id"], r["source"], r["role_name"]) for r in sub["eligible"]] == [
        ("alice-id", "direct", "Owner"),
        ("dave-id", "direct", "Owner"),
    ]
    sp_row = next(r for r in sub["rows"] if r["principal_id"].lower() == "sp-1")
    assert (sp_row["activated"], sp_row["until"][:10]) == (True, "2027-03-01")

    rg = app_with_sqlite.get("/api/scopes/assignments", params={"scope": RG}).json()
    assert {r["source"] for r in rg["eligible"]} == {"inherited"}
    only_direct = app_with_sqlite.get(
        "/api/scopes/assignments", params={"scope": RG, "source": "direct"}
    ).json()
    assert only_direct["eligible"] == []


def test_eligible_only_principal_has_a_page(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    page = app_with_sqlite.get("/api/principals/dave-id")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["assignments"] == [] and body["summary"]["eligible"] == 1
    [row] = body["eligible"]
    assert (row["role_name"], row["eligible"], row["is_high_risk"], row["source"]) == (
        "Owner",
        True,
        True,
        "direct",
    )
    assert "dave-id" in {p["id"] for p in app_with_sqlite.get("/api/principals").json()}
