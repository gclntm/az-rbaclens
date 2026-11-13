"""Settings API — Azure credentials entered through the UI.

Resolution order (used by :func:`app.core.azure.get_credential`):

  1. ``ARM_CLIENT_ID`` / ``ARM_CLIENT_SECRET`` / ``ARM_TENANT_ID`` env vars
  2. Encrypted row in ``azure_credentials`` (id=1)
  3. ``DefaultAzureCredential`` (developer ``az login`` etc.)

The GET endpoint surfaces the current state (which source is active,
whether a secret is set) without ever exposing the secret value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.azure import get_credential
from app.core.config import settings as app_settings
from app.core.crypto import encrypt, encryption_available
from app.db.models import AzureCredentials
from app.db.session import get_session
from app.schemas.settings import CredentialSource, CredentialsRead, CredentialsWrite

router = APIRouter(prefix="/settings", tags=["settings"])

CREDS_ID = 1


def _env_credentials_set() -> bool:
    return bool(
        app_settings.arm_client_id and app_settings.arm_client_secret and app_settings.arm_tenant_id
    )


def _load_row(session: Session) -> AzureCredentials | None:
    return session.get(AzureCredentials, CREDS_ID)


def _resolve_source(row: AzureCredentials | None) -> CredentialSource:
    if _env_credentials_set():
        return "env"
    if row and row.tenant_id and row.client_id and row.client_secret_encrypted:
        return "db"
    return "default"


def _serialize(row: AzureCredentials | None) -> CredentialsRead:
    source = _resolve_source(row)
    if source == "env":
        return CredentialsRead(
            tenant_id=app_settings.arm_tenant_id or None,
            client_id=app_settings.arm_client_id or None,
            client_secret_set=bool(app_settings.arm_client_secret),
            source="env",
            updated_at=None,
            encryption_available=encryption_available(),
        )
    if row is None:
        return CredentialsRead(
            tenant_id=None,
            client_id=None,
            client_secret_set=False,
            source=source,
            updated_at=None,
            encryption_available=encryption_available(),
        )
    return CredentialsRead(
        tenant_id=row.tenant_id,
        client_id=row.client_id,
        client_secret_set=bool(row.client_secret_encrypted),
        source=source,
        updated_at=row.updated_at,
        encryption_available=encryption_available(),
    )


@router.get("/credentials", response_model=CredentialsRead)
def get_credentials(
    session: Annotated[Session, Depends(get_session)],
) -> CredentialsRead:
    return _serialize(_load_row(session))


@router.put("/credentials", response_model=CredentialsRead)
def put_credentials(
    payload: CredentialsWrite,
    session: Annotated[Session, Depends(get_session)],
) -> CredentialsRead:
    """Create or update the singleton credentials row.

    - ``tenant_id`` / ``client_id`` set when provided (non-null).
    - ``client_secret``: ``None`` leaves the existing secret untouched;
      empty string clears it; any other value re-encrypts it.
    """
    row = _load_row(session) or AzureCredentials(id=CREDS_ID)

    if payload.tenant_id is not None:
        row.tenant_id = payload.tenant_id.strip() or None
    if payload.client_id is not None:
        row.client_id = payload.client_id.strip() or None
    if payload.client_secret is not None:
        if payload.client_secret == "":
            row.client_secret_encrypted = None
        else:
            if not encryption_available():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "ENCRYPTION_KEY is not configured on the server, "
                        "cannot persist the client secret. Generate a "
                        "Fernet key and set the ENCRYPTION_KEY env var."
                    ),
                )
            ciphertext = encrypt(payload.client_secret)
            if ciphertext is None:
                raise HTTPException(status_code=503, detail="encryption failed")
            row.client_secret_encrypted = ciphertext

    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()

    # Invalidate the credential cache so the next scan picks up the change.
    get_credential.cache_clear()

    return _serialize(row)


@router.delete("/credentials", response_model=CredentialsRead)
def delete_credentials(
    session: Annotated[Session, Depends(get_session)],
) -> CredentialsRead:
    row = _load_row(session)
    if row is not None:
        session.delete(row)
        session.commit()
    get_credential.cache_clear()
    return _serialize(None)
