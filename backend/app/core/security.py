"""FastAPI auth dependency.

Reads :class:`AuthSettings` on every request. When ``enabled=False`` the
dependency is a no-op (returns ``None``) so the app behaves exactly as
it does today — useful for test / single-tenant dev. When enabled, the
``Authorization: Bearer <jwt>`` header is required and validated via
:mod:`app.core.auth`.

The bootstrap endpoint ``/api/auth/config`` and the settings endpoints
under ``/api/settings/auth`` deliberately stay public so an operator
can enable / reconfigure auth without first being logged in. Health
checks also stay public for orchestrators.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import AuthError, verify_token
from app.db.models import AuthSettings
from app.db.session import get_session

logger = logging.getLogger(__name__)

AUTH_SETTINGS_ID = 1
# What a refused token gets back; the reason is in the log (require_auth).
TOKEN_REFUSED = "sign-in token not accepted: sign in again"  # noqa: S105 - a message


def load_auth_settings(session: Session) -> AuthSettings | None:
    return session.get(AuthSettings, AUTH_SETTINGS_ID)


OPEN_WARNING = (
    "Sign-in is off: anyone who can reach this app can read and change everything in it. "
    "Turn sign-in on (Settings > Authentication), or keep the app on this machine only (BIND_ADDRESS)."
)


def warn_if_open() -> bool:
    """Log a warning when sign-in is off; called at start. Returns whether it is off."""
    # Imported lazily so the tests' replacement of ``SessionLocal`` is picked up.
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as session:
            row = load_auth_settings(session)
    except Exception:  # a warning must never stop the app from starting
        logger.exception("could not read the sign-in settings at start")
        return False
    is_open = row is None or not row.enabled
    if is_open:
        logger.warning(OPEN_WARNING)
    return is_open


def _parse_groups(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [g.strip() for g in raw.split(",") if g.strip()]


def require_auth(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any] | None:
    """Return the validated token claims, or ``None`` when auth is off.

    Raise 401 when auth is on and the request lacks a valid bearer
    token. Raise 500 when auth is on but the settings row is half
    configured (no tenant_id / client_id) — that's a server-side
    misconfiguration the operator must fix in the settings page.

    Side effect: stashes the resolved claims on ``request.state.user``
    so the audit middleware can attribute mutations without re-parsing
    the JWT.
    """
    row = load_auth_settings(session)
    if row is None or not row.enabled:
        request.state.user = None
        return None
    if not row.tenant_id or not row.client_id:
        raise HTTPException(
            status_code=500,
            detail=(
                "Auth is enabled but tenant_id / client_id are not set. "
                "Configure them in Settings → Authentication."
            ),
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_token(
            token,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            allowed_group_ids=_parse_groups(row.allowed_group_ids),
        )
        request.state.user = claims
        return claims
    except AuthError as exc:
        # The reason goes to the log, never to the caller (it would say how to get in).
        logger.warning("sign-in token refused on %s %s: %s", request.method, request.url.path, exc)
        raise HTTPException(
            status_code=401,
            detail=TOKEN_REFUSED,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_admin(
    session: Annotated[Session, Depends(get_session)],
    claims: Annotated[dict[str, Any] | None, Depends(require_auth)] = None,
) -> dict[str, Any] | None:
    """Authorization gate for admin endpoints.

    No-op when auth is disabled (mirrors :func:`require_auth`). When
    auth is on, the token must contain one of the ``admin_group_ids`` in
    its ``groups`` claim. With no admin group set, nobody is an admin: the
    settings page refuses to turn sign-in on without one, and
    ``make auth-off <env>`` is the way back in.
    """
    row = load_auth_settings(session)
    if row is None or not row.enabled:
        return claims
    admin_groups = _parse_groups(row.admin_group_ids)
    if not admin_groups:
        raise HTTPException(
            status_code=403,
            detail="no admin group is set (Settings › Authentication); make auth-off on the host turns sign-in off",
        )
    if claims is None:
        # require_auth already raised; defensive belt-and-suspenders.
        raise HTTPException(status_code=401, detail="missing bearer token")
    token_groups = set(claims.get("groups") or [])
    if not token_groups.intersection(admin_groups):
        raise HTTPException(
            status_code=403,
            detail=(
                "this endpoint requires admin group membership "
                "(see Settings → Authentication → Admin group IDs)"
            ),
        )
    return claims


def who_is(session: Session, claims: dict[str, Any] | None) -> tuple[str | None, str | None, bool]:
    """``(object id, name, is_admin)`` for a request's claims.

    With sign-in off (``claims`` is None) everyone is an unnamed admin,
    as :func:`require_admin` treats them.
    """
    if claims is None:
        return None, None, True
    oid = (claims.get("oid") or "").lower() or None
    name = claims.get("name") or claims.get("preferred_username")
    row = load_auth_settings(session)
    admin_groups = set(_parse_groups(row.admin_group_ids if row else None))
    # No admin group set: nobody is an admin (require_admin).
    is_admin = bool(admin_groups & set(claims.get("groups") or []))
    return oid, name, is_admin
