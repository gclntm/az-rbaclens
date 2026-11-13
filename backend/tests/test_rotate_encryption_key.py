"""Key rotation re-encrypts every stored secret, all or nothing (scripts/rotate_encryption_key.py)."""

from __future__ import annotations

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _seed(old: Fernet) -> None:
    from app.db.models import AppSetting, AzureCredentials
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        db.add(
            AzureCredentials(
                id=1,
                tenant_id="t",
                client_id="c",
                client_secret_encrypted=old.encrypt(b"azure-secret"),
            )
        )
        db.add(
            AppSetting(
                key="ai",
                value={
                    "enabled": True,
                    "model": "claude-opus-5-5",
                    "key_encrypted": old.encrypt(b"anthropic-key").decode(),
                },
            )
        )
        db.commit()


def _stored() -> tuple[bytes, bytes]:
    from app.db.models import AppSetting, AzureCredentials
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return (
            db.get(AzureCredentials, 1).client_secret_encrypted,
            db.get(AppSetting, "ai").value["key_encrypted"].encode(),
        )


def test_both_secrets_are_rotated(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal
    from scripts.rotate_encryption_key import rotate

    old, new = Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())
    _seed(old)
    with SessionLocal() as db:
        assert rotate(db, old, new, dry_run=True) == 0
    azure, ai = _stored()
    assert old.decrypt(azure) == b"azure-secret"  # a dry run writes nothing

    with SessionLocal() as db:
        assert rotate(db, old, new) == 0
    azure, ai = _stored()
    assert (new.decrypt(azure), new.decrypt(ai)) == (b"azure-secret", b"anthropic-key")


def test_a_wrong_old_key_changes_nothing(app_with_sqlite: TestClient):
    from app.db.session import SessionLocal
    from scripts.rotate_encryption_key import rotate

    old = Fernet(Fernet.generate_key())
    _seed(old)
    before = _stored()
    with SessionLocal() as db:
        assert rotate(db, Fernet(Fernet.generate_key()), Fernet(Fernet.generate_key())) == 2
    assert _stored() == before
