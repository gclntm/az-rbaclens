"""Auth-related endpoints.

* ``GET /api/auth/config`` — public. Tells the SPA whether sign-in is
  on and which tenant / client to point MSAL at. Chicken-and-egg: the
  browser has to know whether to log in *before* it can log in.

* ``GET / PUT /api/settings/auth`` — admins only once sign-in is on
  (``require_admin``, applied where ``settings_router`` is mounted). While
  sign-in is off they stay open, so it can be set up without signing in.
  If a wrong setting locks everyone out, ``make auth-off <env>`` turns
  sign-in off from the host (``scripts/auth_off.py``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.security import AUTH_SETTINGS_ID, OPEN_WARNING, load_auth_settings
from app.db.models import AuthSettings
from app.db.session import get_session
from app.schemas.settings import (
    AuthConfigPublic,
    AuthSettingsRead,
    AuthSettingsWrite,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])  # public: the SPA's bootstrap config
settings_router = APIRouter(tags=["auth"])  # mounted with require_admin


def _serialize_public(row: AuthSettings | None) -> AuthConfigPublic:
    if row is None or not row.enabled:
        return AuthConfigPublic(enabled=False)
    return AuthConfigPublic(
        enabled=True,
        tenant_id=row.tenant_id,
        client_id=row.client_id,
        authority=(f"https://login.microsoftonline.com/{row.tenant_id}" if row.tenant_id else None),
    )


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


def _serialize_full(row: AuthSettings | None) -> AuthSettingsRead:
    if row is None:
        return AuthSettingsRead()
    return AuthSettingsRead(
        enabled=row.enabled,
        tenant_id=row.tenant_id,
        client_id=row.client_id,
        allowed_group_ids=_split_csv(row.allowed_group_ids),
        admin_group_ids=_split_csv(row.admin_group_ids),
        updated_at=row.updated_at,
    )


@router.get("/auth/config", response_model=AuthConfigPublic)
def get_auth_config(
    session: Annotated[Session, Depends(get_session)],
) -> AuthConfigPublic:
    """Public bootstrap config for the SPA."""
    return _serialize_public(load_auth_settings(session))


@settings_router.get("/settings/auth", response_model=AuthSettingsRead)
def get_auth_settings(
    session: Annotated[Session, Depends(get_session)],
) -> AuthSettingsRead:
    return _serialize_full(load_auth_settings(session))


@settings_router.put("/settings/auth", response_model=AuthSettingsRead)
def put_auth_settings(
    payload: AuthSettingsWrite,
    session: Annotated[Session, Depends(get_session)],
) -> AuthSettingsRead:
    row = load_auth_settings(session) or AuthSettings(id=AUTH_SETTINGS_ID)
    was_on = bool(row.enabled)

    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.tenant_id is not None:
        row.tenant_id = payload.tenant_id.strip() or None
    if payload.client_id is not None:
        row.client_id = payload.client_id.strip() or None
    if payload.allowed_group_ids is not None:
        cleaned = [g.strip() for g in payload.allowed_group_ids if g.strip()]
        row.allowed_group_ids = ",".join(cleaned) if cleaned else None
    if payload.admin_group_ids is not None:
        cleaned = [g.strip() for g in payload.admin_group_ids if g.strip()]
        row.admin_group_ids = ",".join(cleaned) if cleaned else None

    if row.enabled and not (row.tenant_id and row.client_id):
        raise HTTPException(
            status_code=400, detail="set the tenant and client ids before turning sign-in on"
        )
    if row.enabled and not row.admin_group_ids:
        # Without one, every signed-in user of the tenant would be an admin.
        raise HTTPException(
            status_code=400, detail="set at least one admin group before turning sign-in on"
        )

    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    if was_on and not row.enabled:
        logger.warning(OPEN_WARNING)
    return _serialize_full(row)
