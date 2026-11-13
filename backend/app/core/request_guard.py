"""Refuse requests a browser sends on behalf of another website.

With sign-in off the app has no secret a browser must present, so any web
page the operator has open could otherwise change things here: the browser
sends the request for it. Two checks close that, on every request:

* **Host.** The ``Host`` header must be a name this app answers to:
  ``localhost``, ``*.local``, an IP address, or a name listed in
  ``EXTRA_SERVER_NAMES`` (the same rule as the frontend's nginx). This stops
  DNS rebinding, where a foreign name is pointed at this machine so the
  browser treats the app as part of the attacker's site.
* **Origin.** A state-changing request (POST / PUT / PATCH / DELETE) that
  names where it comes from must come from this app: its ``Origin`` equals
  the host it was sent to, or is listed in ``CORS_ORIGINS``. A request with
  no ``Origin`` is not from a browser page (curl, a script) and passes,
  unless the browser itself marks it cross-site (``Sec-Fetch-Site``).

Reads are not checked for origin: a browser already keeps another site from
reading the answer (CORS).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
_LOCAL_NAMES = {"localhost"}
_NAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)

HOST_REFUSED = "This app does not answer to that host name."
ORIGIN_REFUSED = "This request came from another website and was refused."


def host_name(host_header: str) -> str:
    """The name part of a ``Host`` header, lowercased: no port, no IPv6 brackets."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if ":" in host else host


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def extra_names(raw: str) -> set[str]:
    """``EXTRA_SERVER_NAMES`` (space-separated) as a set; anything that isn't a host name is ignored."""
    return {n for n in raw.lower().split() if _NAME.match(n)}


def host_allowed(host_header: str | None, extra: set[str]) -> bool:
    if not host_header:
        return False
    name = host_name(host_header)
    if not name:
        return False
    if _is_ip(name) or name in _LOCAL_NAMES or name in extra:
        return True
    return bool(_NAME.match(name)) and name.endswith(".local")


def from_this_app(origin: str, host_header: str, allowed_origins: list[str]) -> bool:
    """Whether ``Origin`` is the address the request was sent to, or one listed in ``CORS_ORIGINS``."""
    origin = origin.strip().rstrip("/").lower()
    if origin in {o.rstrip("/").lower() for o in allowed_origins}:
        return True
    parts = urlsplit(origin)
    return (
        parts.scheme in ("http", "https")
        and bool(parts.netloc)
        and parts.netloc == host_header.strip().lower()
    )


def refusal(method: str, headers: Headers) -> tuple[int, str] | None:
    """``(status, message)`` when the request must be refused, else ``None``."""
    host = headers.get("host")
    if not host_allowed(host, extra_names(settings.extra_server_names)):
        return 400, HOST_REFUSED
    if method.upper() not in _CHANGING:
        return None
    origin = headers.get("origin")
    if origin is not None:
        return (
            None
            if from_this_app(origin, host or "", settings.cors_origins)
            else (403, ORIGIN_REFUSED)
        )
    if (headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return 403, ORIGIN_REFUSED
    return None


class RequestGuardMiddleware:
    """ASGI middleware applying :func:`refusal` to every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        refused = refusal(scope["method"], headers)
        if refused is None:
            await self.app(scope, receive, send)
            return
        status, message = refused
        # repr and a cut: these headers are the caller's text, and it goes to the log.
        logger.warning(
            "refused %s %s: host=%.100r origin=%.200r (%s)",
            scope["method"],
            scope["path"],
            headers.get("host"),
            headers.get("origin"),
            message,
        )
        await JSONResponse({"detail": message}, status_code=status)(scope, receive, send)
