"""The token check itself, with tokens signed by a throwaway key (core/auth.py)."""

from __future__ import annotations

import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.core import auth

TENANT = "11111111-1111-1111-1111-111111111111"
CLIENT = "22222222-2222-2222-2222-222222222222"
ADMINS = "44444444-4444-4444-4444-444444444444"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(kid: str = "k1", key=KEY, exp_in: int = 3600, **over) -> str:
    """A token signed with ``key``; ``exp_in`` is seconds from now, counted when it is made."""
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "azp": CLIENT,
        "iat": now,
        "nbf": now,
        "exp": now + exp_in,
        "oid": "ada-oid",
        "scp": "User.Read",
        "preferred_username": "ada@example.org",
        "groups": [ADMINS],
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def keys(monkeypatch):
    """The tenant's signing keys: k1 is KEY; counts how often they are fetched."""
    fetched = []

    def fetch(tenant_id):
        fetched.append(tenant_id)
        return {"k1": KEY.public_key()}

    monkeypatch.setattr(auth, "_fetch_keys", fetch)
    monkeypatch.setattr(auth, "_KEYS", {})
    return fetched


def test_a_persons_token_from_this_app_is_accepted(keys):
    claims = auth.verify_token(_token(), TENANT, CLIENT, [ADMINS])
    assert claims["oid"] == "ada-oid"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        # Offsets, not times: parameters are built when the tests are collected,
        # which can be well before this test runs.
        ({"exp_in": -30}, None),  # 30 s late: within the minute allowed
        ({"exp_in": -120}, "expired"),
        ({"aud": "33333333-3333-3333-3333-333333333333"}, "audience"),
        ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "issuer"),
        ({"azp": "55555555-5555-5555-5555-555555555555"}, "another app"),
        ({"scp": None, "preferred_username": None}, "app-only"),  # client credentials
        ({"idtyp": "app"}, "app-only"),
        ({"groups": []}, "allowed groups"),
    ],
)
def test_what_is_refused(keys, over, reason):
    token = _token(**over)
    if reason is None:
        assert auth.verify_token(token, TENANT, CLIENT, [ADMINS])["oid"] == "ada-oid"
        return
    with pytest.raises(auth.AuthError, match=f"(?i){reason}"):
        auth.verify_token(token, TENANT, CLIENT, [ADMINS])


def test_a_token_signed_by_another_key_or_alg_is_refused(keys):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(auth.AuthError, match="invalid token"):
        auth.verify_token(_token(key=other), TENANT, CLIENT)
    unsigned = jwt.encode({"aud": CLIENT}, "secret", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(auth.AuthError, match="unexpected token header"):
        auth.verify_token(unsigned, TENANT, CLIENT)


def test_an_unknown_key_fetches_the_keys_again_at_most_once_a_minute(keys):
    clock = 1_000_000.0
    assert auth.signing_key(TENANT, "k1", now=clock) is not None
    assert len(keys) == 1
    for _ in range(5):
        with pytest.raises(auth.AuthError, match="unknown signing key"):
            auth.signing_key(TENANT, "k9", now=clock + 10)
    assert len(keys) == 1  # within the minute: not fetched again
    with pytest.raises(auth.AuthError):
        auth.signing_key(TENANT, "k9", now=clock + auth.KEYS_REFETCH_SECONDS + 11)
    assert len(keys) == 2
    auth.signing_key(
        TENANT, "k1", now=clock + auth.KEYS_TTL_SECONDS + 100
    )  # a day later: fetched again
    assert len(keys) == 3


def _sign_in_on(app: TestClient, admin_groups: list[str] | None = None) -> None:
    from app.core.security import AUTH_SETTINGS_ID
    from app.db.models import AuthSettings
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.add(
            AuthSettings(
                id=AUTH_SETTINGS_ID,
                enabled=True,
                tenant_id=TENANT,
                client_id=CLIENT,
                admin_group_ids=",".join(admin_groups if admin_groups is not None else [ADMINS]),
            )
        )
        db.commit()


def test_a_refused_token_gets_a_fixed_answer_and_the_reason_goes_to_the_log(
    app_with_sqlite: TestClient, keys, caplog
):
    from app.core.security import TOKEN_REFUSED

    _sign_in_on(app_with_sqlite)
    ok = app_with_sqlite.get(
        "/api/iam-access/scans", headers={"Authorization": f"Bearer {_token()}"}
    )
    assert ok.status_code == 200
    with caplog.at_level(logging.WARNING, logger="app.core.security"):
        r = app_with_sqlite.get(
            "/api/iam-access/scans", headers={"Authorization": f"Bearer {_token(idtyp='app')}"}
        )
    assert (r.status_code, r.json()["detail"]) == (401, TOKEN_REFUSED)
    assert "app-only token" in caplog.text
    assert _token(idtyp="app") not in caplog.text  # never the token itself


def test_without_an_admin_group_nobody_is_an_admin(app_with_sqlite: TestClient, keys):
    _sign_in_on(app_with_sqlite, admin_groups=[])
    headers = {"Authorization": f"Bearer {_token()}"}
    assert app_with_sqlite.get("/api/settings/auth", headers=headers).status_code == 403
    assert app_with_sqlite.get("/api/me", headers=headers).json()["is_admin"] is False


@pytest.mark.parametrize("field", ["tenant_id", "client_id"])
def test_sign_in_settings_take_guids_only(app_with_sqlite: TestClient, field):
    assert app_with_sqlite.put("/api/settings/auth", json={field: "not-a-guid"}).status_code == 422
    ok = app_with_sqlite.put("/api/settings/auth", json={field: TENANT.upper()})
    assert ok.status_code == 200 and ok.json()[field] == TENANT  # lowercased
    assert (
        app_with_sqlite.put("/api/settings/auth", json={field: ""}).json()[field] is None
    )  # cleared
    bad = app_with_sqlite.put("/api/settings/auth", json={"admin_group_ids": [ADMINS, "admins"]})
    assert bad.status_code == 422
