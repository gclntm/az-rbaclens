"""Audit logging for mutating /api/* requests.

Records one row per POST / PUT / PATCH / DELETE under /api/*, capturing
actor (from the request's JWT claims when auth is on, ``anonymous``
otherwise), the caller's address, HTTP method, path, and response status. Read-only requests
and the unauthenticated bootstrap endpoints are *not* logged (would
swamp the table without adding security value).

Body content is deliberately *not* captured — payloads can contain
secrets (e.g. client_secret on the credentials endpoint). For an
"investigative" log of credential changes, look at ``method='PUT'`` +
``path='/api/settings/credentials'`` and infer "the secret changed at
this time" from the row's existence.

The address is ``request.client.host``. Behind nginx (prod) that is nginx's
container unless uvicorn trusts the proxy: the prod stack sets
``FORWARDED_ALLOW_IPS``, and nginx overwrites ``X-Forwarded-For`` with the
address it saw, so a caller can't put another address in the log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app.db.models import AuditEvent

logger = logging.getLogger(__name__)

_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _actor_from_request(request: Request) -> tuple[str, str | None]:
    """Pull (actor_id, actor_display_name) from request.state.user.

    ``require_auth`` stores the validated JWT claims on
    ``request.state.user``. Anonymous = the dependency hasn't run for
    this path (public endpoints), or auth is globally disabled.
    """
    claims = getattr(request.state, "user", None)
    if not claims:
        return "anonymous", None
    actor_id = (
        claims.get("oid") or claims.get("sub") or claims.get("preferred_username") or "unknown"
    )
    actor_name = claims.get("preferred_username") or claims.get("name")
    return actor_id, actor_name


def _client_ip(request: Request) -> str | None:
    """The caller's address, cut to the column's size (an IPv6 address fits)."""
    return request.client.host[:45] if request.client else None


def _write(event: AuditEvent) -> None:
    # Imported lazily so the test fixture's monkeypatch of
    # ``app.db.session.SessionLocal`` is picked up; importing at
    # module load time would bind to the production binding.
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        session.add(event)
        session.commit()


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        response = await call_next(request)

        if request.method not in _AUDIT_METHODS:
            return response
        path = request.url.path
        if not path.startswith("/api/"):
            return response

        try:
            actor_id, actor_name = _actor_from_request(request)
            event = AuditEvent(
                actor=actor_id,
                actor_name=actor_name,
                method=request.method,
                path=path,
                status_code=response.status_code,
                client_ip=_client_ip(request),
            )
            # In a worker thread: the write is synchronous, and a slow
            # database must not freeze the event loop for every request.
            await run_in_threadpool(_write, event)
        except Exception:
            # Audit failures must never break the actual request — log
            # and move on. Worst case, a mutation isn't recorded; the
            # response is still correct.
            logger.exception("audit log write failed for %s %s", request.method, path)

        return response
