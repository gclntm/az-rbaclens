"""GET /grants/{grant_id} — one grant in plain words; ids stable across checks."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.findings import grant_fingerprint
from tests.test_registry import MG, OWNER, READER, RG, SUB_SCOPE, _arm, _seed_world


def _world(client: TestClient) -> int:
    from app.db.models import Principal, RoleAssignment
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        scan_id = _seed_world(db)
        owner = db.query(RoleAssignment).filter(RoleAssignment.scope == MG).one()
        owner.created_on = datetime(2026, 11, 3, 14, 12, tzinfo=UTC)
        owner.created_by_name = "sp-terraform"
        owner.created_by_type = "ServicePrincipal"
        db.commit()
    client.post("/api/settings/registry/rebuild")
    client.post(f"/api/iam-access/scans/{scan_id}/findings/recompute")
    with SessionLocal() as db:
        db.get(Principal, "sp-1").last_activity_at = datetime(2026, 11, 7, tzinfo=UTC)
        db.commit()
    return scan_id


def test_grant_id_is_on_rows_and_opens_the_grant(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    caps = app_with_sqlite.get("/api/scopes/capabilities", params={"scope": SUB_SCOPE}).json()
    grant = caps["levels"][0]["identities"][0]["grants"][0]
    assert grant["grant_id"] == grant_fingerprint(MG, _arm(OWNER), "SP-1")

    r = app_with_sqlite.get(f"/api/grants/{grant['grant_id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["principal_name"], d["kind"], d["role_name"], d["level"]) == (
        "sp-one",
        "app",
        "Owner",
        "access",
    )
    assert [p["key"] for p in d["powers"] if p["allowed"]] == ["access", "keys", "change", "view"]
    assert (d["scope_name"], d["scope_level"], d["ancestors"]) == ("Root MG", "mgmt_group", [])
    assert d["inside_count"] >= 2  # prod and rg-app at least
    assert d["since"].startswith("2026-11-03") and d["given_by_type"] == "ServicePrincipal"
    assert d["last_activity_at"].startswith("2026-11-07")
    assert d["access_as_of"] is not None
    assert [f["category"] for f in d["findings"]] == ["high_risk_broad_scope"]


def test_group_grant_counts_members_and_reader_powers(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    gid = grant_fingerprint(RG, _arm(READER), "grp-1")
    d = app_with_sqlite.get(f"/api/grants/{gid}").json()
    assert (d["kind"], d["member_count"], d["level"]) == ("group", 2, "view")
    assert [p["key"] for p in d["powers"] if p["allowed"]] == ["view"]
    assert [a["name"] for a in d["ancestors"]] == ["Root MG", "prod"]
    assert d["since"] is None and d["given_by_name"] is None


def test_unknown_grant_is_404(app_with_sqlite: TestClient):
    _world(app_with_sqlite)
    r = app_with_sqlite.get("/api/grants/deadbeef")
    assert r.status_code == 404
