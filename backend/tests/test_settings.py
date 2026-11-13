"""Tests for the settings API + Azure credential resolution.

Uses the ``app_with_sqlite`` fixture from conftest. The ``cryptography``
library + Fernet are real (no mocks) so we validate the round-trip
encryption / decryption against the same code path the live app uses.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture
def with_encryption_key(monkeypatch: pytest.MonkeyPatch):
    """Set a valid Fernet key for the duration of one test."""
    key = Fernet.generate_key().decode()
    from app.core import config

    monkeypatch.setattr(config.settings, "encryption_key", key)
    return key


@pytest.fixture
def without_env_creds(monkeypatch: pytest.MonkeyPatch):
    """Ensure ARM_* env vars are empty so the resolver can fall through."""
    from app.core import config

    monkeypatch.setattr(config.settings, "arm_client_id", "")
    monkeypatch.setattr(config.settings, "arm_client_secret", "")
    monkeypatch.setattr(config.settings, "arm_tenant_id", "")


def test_crypto_roundtrip(with_encryption_key):
    from app.core.crypto import decrypt, encrypt, encryption_available

    assert encryption_available()
    ciphertext = encrypt("hunter2")
    assert ciphertext is not None and ciphertext != b"hunter2"
    assert decrypt(ciphertext) == "hunter2"


def test_crypto_returns_none_without_key(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "encryption_key", "")
    from app.core.crypto import encrypt, encryption_available

    assert encryption_available() is False
    assert encrypt("anything") is None


def test_get_credentials_when_env_set(app_with_sqlite: TestClient, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "arm_tenant_id", "tenant-123")
    monkeypatch.setattr(config.settings, "arm_client_id", "client-456")
    monkeypatch.setattr(config.settings, "arm_client_secret", "secret-xyz")

    body = app_with_sqlite.get("/api/settings/credentials").json()
    assert body["source"] == "env"
    assert body["tenant_id"] == "tenant-123"
    assert body["client_id"] == "client-456"
    assert body["client_secret_set"] is True


def test_put_credentials_persists_encrypted(
    app_with_sqlite: TestClient, with_encryption_key, without_env_creds
):
    """The persisted client_secret must be ciphertext, never plaintext."""
    from app.db.models import AzureCredentials
    from app.db.session import SessionLocal

    response = app_with_sqlite.put(
        "/api/settings/credentials",
        json={
            "tenant_id": "tenant-aaa",
            "client_id": "client-bbb",
            "client_secret": "super-secret",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "db"
    assert body["client_secret_set"] is True
    assert "super-secret" not in response.text  # never expose the plaintext

    with SessionLocal() as session:
        row = session.get(AzureCredentials, 1)
        assert row is not None
        assert row.client_secret_encrypted is not None
        assert row.client_secret_encrypted != b"super-secret"
        # Decrypting with the same key recovers it.
        from app.core.crypto import decrypt

        assert decrypt(row.client_secret_encrypted) == "super-secret"


def test_put_credentials_503_when_no_encryption_key(
    app_with_sqlite: TestClient, monkeypatch, without_env_creds
):
    from app.core import config

    monkeypatch.setattr(config.settings, "encryption_key", "")

    response = app_with_sqlite.put(
        "/api/settings/credentials",
        json={"tenant_id": "t", "client_id": "c", "client_secret": "s"},
    )
    assert response.status_code == 503
    assert "ENCRYPTION_KEY" in response.text


def test_put_credentials_keeps_secret_when_omitted(
    app_with_sqlite: TestClient, with_encryption_key, without_env_creds
):
    """Sending null/missing client_secret must NOT clear the existing one."""
    app_with_sqlite.put(
        "/api/settings/credentials",
        json={"tenant_id": "t1", "client_id": "c1", "client_secret": "first"},
    )
    # Update tenant only, leave secret alone.
    response = app_with_sqlite.put(
        "/api/settings/credentials",
        json={"tenant_id": "t2"},
    )
    body = response.json()
    assert body["tenant_id"] == "t2"
    assert body["client_id"] == "c1"
    assert body["client_secret_set"] is True


def test_put_credentials_clears_secret_with_empty_string(
    app_with_sqlite: TestClient, with_encryption_key, without_env_creds
):
    app_with_sqlite.put(
        "/api/settings/credentials",
        json={"tenant_id": "t", "client_id": "c", "client_secret": "first"},
    )
    response = app_with_sqlite.put(
        "/api/settings/credentials",
        json={"client_secret": ""},
    )
    body = response.json()
    assert body["client_secret_set"] is False


def test_delete_credentials_clears_row(
    app_with_sqlite: TestClient, with_encryption_key, without_env_creds
):
    app_with_sqlite.put(
        "/api/settings/credentials",
        json={"tenant_id": "t", "client_id": "c", "client_secret": "x"},
    )
    response = app_with_sqlite.delete("/api/settings/credentials")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "default"
    assert body["client_secret_set"] is False
