"""Tests for the auth bootstrap + settings endpoints and the dependency.

These don't exercise the JWT path itself (that would require generating
a signed token against a fake JWKS — overkill for a smoke test). They
verify the "auth disabled by default" contract, the settings round-trip,
and that turning auth on without a valid token returns 401.
"""

from fastapi.testclient import TestClient

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
G1 = "33333333-3333-3333-3333-333333333331"
G2 = "33333333-3333-3333-3333-333333333332"
ADMINS = "44444444-4444-4444-4444-444444444444"


def test_auth_disabled_by_default(app_with_sqlite: TestClient):
    """Fresh DB → no auth_settings row → SPA sees enabled=false."""
    response = app_with_sqlite.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "tenant_id": None,
        "client_id": None,
        "authority": None,
    }


def test_protected_routes_open_when_auth_disabled(app_with_sqlite: TestClient):
    """When auth is off, protected routes work without an Authorization header."""
    response = app_with_sqlite.get("/api/iam-access/scans")
    assert response.status_code == 200


def test_settings_round_trip(app_with_sqlite: TestClient):
    payload = {
        "enabled": False,
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "allowed_group_ids": [G1, G2],
    }
    response = app_with_sqlite.put("/api/settings/auth", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["tenant_id"] == TENANT
    assert body["client_id"] == CLIENT
    assert body["allowed_group_ids"] == [G1, G2]

    get_response = app_with_sqlite.get("/api/settings/auth")
    assert get_response.status_code == 200
    assert get_response.json()["allowed_group_ids"] == [G1, G2]


def test_public_config_reflects_enabled(app_with_sqlite: TestClient):
    app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "enabled": True,
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "admin_group_ids": [ADMINS],
        },
    )
    response = app_with_sqlite.get("/api/auth/config")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "enabled": True,
        "tenant_id": TENANT,
        "client_id": CLIENT,
        "authority": f"https://login.microsoftonline.com/{TENANT}",
    }


def test_protected_route_requires_token_when_auth_enabled(app_with_sqlite: TestClient):
    app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "enabled": True,
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "admin_group_ids": [ADMINS],
        },
    )
    # No Authorization header → 401.
    response = app_with_sqlite.get("/api/iam-access/scans")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"].lower().startswith("bearer")


def test_health_still_open_when_auth_enabled(app_with_sqlite: TestClient):
    app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "enabled": True,
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "admin_group_ids": [ADMINS],
        },
    )
    response = app_with_sqlite.get("/api/health")
    assert response.status_code == 200


def test_admin_groups_round_trip(app_with_sqlite: TestClient):
    response = app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "enabled": False,
            "tenant_id": TENANT,
            "client_id": CLIENT,
            "admin_group_ids": [ADMINS],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["admin_group_ids"] == [ADMINS]


def test_admin_routes_open_when_auth_disabled(app_with_sqlite: TestClient):
    """When auth is off, admin routes work without any token."""
    response = app_with_sqlite.get("/api/settings/credentials")
    assert response.status_code == 200
    response = app_with_sqlite.get("/api/settings/database/stats")
    assert response.status_code in (200, 500)
    # 500 is fine here — SQLite test DB may not implement everything
    # the postgres-only stats query needs. The point is it isn't 401/403.


def test_sign_in_cannot_be_turned_on_without_an_admin_group(app_with_sqlite: TestClient):
    """Without one, every signed-in user of the tenant would be an admin."""
    r = app_with_sqlite.put(
        "/api/settings/auth", json={"enabled": True, "tenant_id": TENANT, "client_id": CLIENT}
    )
    assert r.status_code == 400 and "admin group" in r.json()["detail"]
    assert app_with_sqlite.get("/api/auth/config").json()["enabled"] is False  # nothing was saved


def test_sign_in_cannot_be_turned_on_without_tenant_and_client(app_with_sqlite: TestClient):
    r = app_with_sqlite.put(
        "/api/settings/auth", json={"enabled": True, "admin_group_ids": [ADMINS]}
    )
    assert r.status_code == 400 and "tenant and client" in r.json()["detail"]
