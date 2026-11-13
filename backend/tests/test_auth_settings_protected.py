"""The sign-in settings need an admin once sign-in is on, and the host can always turn it off.

Regression tests for security assessment finding SEC-01 (2026-11-09). The
pattern is reusable: for every endpoint that changes how people sign in or
who is an admin, assert it refuses no token (401) and a non-admin (403), and
that a host-side way back in exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PAYLOAD = {
    "enabled": False,
    "tenant_id": "99999999-0000-0000-0000-000000000001",
    "client_id": "99999999-0000-0000-0000-000000000002",
}


@pytest.mark.parametrize("method", ["get", "put"])
def test_no_token_is_refused_once_sign_in_is_on(app_with_sqlite: TestClient, signed_in, method):
    signed_in("anyone")  # turns sign-in on
    kwargs = {"json": PAYLOAD} if method == "put" else {}
    response = getattr(app_with_sqlite, method)("/api/settings/auth", **kwargs)
    assert response.status_code == 401


def test_a_signed_in_non_admin_is_refused(app_with_sqlite: TestClient, signed_in):
    bob = signed_in("bob-id")
    assert app_with_sqlite.put("/api/settings/auth", json=PAYLOAD, headers=bob).status_code == 403
    assert app_with_sqlite.get("/api/settings/auth", headers=bob).status_code == 403


def test_an_admin_can_change_them(app_with_sqlite: TestClient, signed_in):
    adm = signed_in("admin-id", admin=True)
    response = app_with_sqlite.put(
        "/api/settings/auth",
        json={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": "22222222-2222-2222-2222-222222222222",
        },
        headers=adm,
    )
    assert (
        response.status_code == 200
        and response.json()["tenant_id"] == "11111111-1111-1111-1111-111111111111"
    )


def test_the_bootstrap_config_stays_public(app_with_sqlite: TestClient, signed_in):
    signed_in("anyone")
    assert app_with_sqlite.get("/api/auth/config").status_code == 200


def test_the_host_can_turn_sign_in_off(app_with_sqlite: TestClient, capsys):
    from app.core.security import AUTH_SETTINGS_ID, load_auth_settings
    from app.db.models import AuthSettings
    from app.db.session import SessionLocal
    from scripts import auth_off

    with SessionLocal() as db:
        db.add(
            AuthSettings(
                id=AUTH_SETTINGS_ID,
                enabled=True,
                tenant_id="t",
                client_id="c",
                admin_group_ids="admins",
            )
        )
        db.commit()

    assert auth_off.main() == 0
    with SessionLocal() as db:
        row = load_auth_settings(db)
        assert row.enabled is False and (row.tenant_id, row.client_id) == ("t", "c")  # ids kept
    assert auth_off.main() == 0  # already off: nothing to do
    assert "already off" in capsys.readouterr().out
