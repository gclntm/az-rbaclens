"""Microsoft Entra ID (Azure AD) access-token validation.

Used by the FastAPI dependency :func:`require_auth` to verify the
``Authorization: Bearer <jwt>`` header attached by the SPA after a
successful MSAL sign-in. A token is accepted when:

- its signature checks against the tenant's published signing keys
  (``RS256`` only), and ``iss`` / ``aud`` / ``exp`` are right, with
  :data:`CLOCK_LEEWAY_SECONDS` of clock drift allowed;
- it was issued to this app's own sign-in (``azp`` / ``appid`` is the
  client id), and to a person: an app-only token (client credentials) is
  refused, whoever holds it;
- when allowed groups are set, the ``groups`` claim has one of them.

Signing keys are fetched with the corporate CA bundle (like every other
outbound call), kept :data:`KEYS_TTL_SECONDS`, and fetched again early only
when a token names a key not on file, at most once every
:data:`KEYS_REFETCH_SECONDS`: an unknown key id can't make the app hammer
login.microsoftonline.com.

:class:`AuthError` carries the reason for the log; callers answer with a
fixed text, so a caller learns nothing about why a token was refused.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from app.core.azure import ca_bundle_path

logger = logging.getLogger(__name__)

KEYS_TTL_SECONDS = 24 * 3600
KEYS_REFETCH_SECONDS = 60
CLOCK_LEEWAY_SECONDS = 60


class AuthError(Exception):
    """Raised when token validation fails. Caller maps to HTTP 401."""


@dataclass
class _Keys:
    by_kid: dict[str, Any] = field(default_factory=dict)
    fetched_at: float = 0.0


_KEYS: dict[str, _Keys] = {}
_LOCK = threading.Lock()


def _jwks_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


def _issuer(tenant_id: str) -> str:
    # v2.0 issuer — matches the ``iss`` claim of MSAL-issued v2 tokens.
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


def _expected_audience(client_id: str) -> list[str]:
    # MSAL's default in browser flows uses the client_id directly as the
    # audience when no Application ID URI is configured. Accept both.
    return [client_id, f"api://{client_id}"]


def _fetch_keys(tenant_id: str) -> dict[str, Any]:
    """The tenant's signing keys by key id, fetched through the CA bundle."""
    verify: bool | str = ca_bundle_path() or True
    with httpx.Client(timeout=10.0, verify=verify) as client:
        resp = client.get(_jwks_url(tenant_id))
        resp.raise_for_status()
        body = resp.json()
    out: dict[str, Any] = {}
    for jwk in body.get("keys") or []:
        kid = jwk.get("kid")
        if not kid or jwk.get("kty") != "RSA":
            continue
        try:
            out[kid] = jwt.PyJWK(jwk, algorithm="RS256").key
        except jwt.PyJWKError:
            logger.warning("signing key %s of tenant %s could not be read; skipped", kid, tenant_id)
    return out


def signing_key(tenant_id: str, kid: str, now: float | None = None) -> Any:
    """The key named ``kid``; fetches the keys when they are old, or when ``kid`` is
    unknown and the last fetch is more than a minute old. Raises :class:`AuthError`."""
    now = time.time() if now is None else now
    with _LOCK:
        keys = _KEYS.get(tenant_id)
        stale = keys is None or now - keys.fetched_at > KEYS_TTL_SECONDS
        unknown = keys is not None and kid not in keys.by_kid
        if stale or (unknown and now - keys.fetched_at > KEYS_REFETCH_SECONDS):
            try:
                keys = _Keys(by_kid=_fetch_keys(tenant_id), fetched_at=now)
            except (httpx.HTTPError, ValueError) as exc:
                raise AuthError(
                    f"signing keys could not be fetched: {type(exc).__name__}: {exc}"
                ) from exc
            _KEYS[tenant_id] = keys
    key = keys.by_kid.get(kid)
    if key is None:
        raise AuthError(f"unknown signing key {kid!r}")
    return key


def is_user_token(claims: dict[str, Any]) -> bool:
    """Delegated (a person signed in), not app-only. App-only tokens never carry
    ``scp``, nor a user name; ``idtyp`` says so outright when Entra adds it."""
    if claims.get("idtyp") == "app":
        return False
    if claims.get("scp"):
        return True
    return any(claims.get(k) for k in ("preferred_username", "upn", "unique_name"))


def verify_token(
    token: str,
    tenant_id: str,
    client_id: str,
    allowed_group_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Validate an Azure AD access token and return its claims.

    Raises :class:`AuthError` on any failure, with the reason (for the log).
    """
    if not token:
        raise AuthError("missing bearer token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"malformed token: {exc}") from exc
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise AuthError(f"unexpected token header: alg={header.get('alg')!r}")
    key = signing_key(tenant_id, header["kid"])
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=_expected_audience(client_id),
            issuer=_issuer(tenant_id),
            leeway=CLOCK_LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    issued_to = claims.get("azp") or claims.get("appid")
    if issued_to and str(issued_to).lower() != client_id.lower():
        raise AuthError(f"token issued to another app ({issued_to})")
    if not is_user_token(claims):
        raise AuthError("app-only token: a person must sign in")

    if allowed_group_ids:
        token_groups = set(claims.get("groups") or [])
        if not token_groups.intersection(allowed_group_ids):
            raise AuthError("the token's groups claim has none of the allowed groups")

    return claims
