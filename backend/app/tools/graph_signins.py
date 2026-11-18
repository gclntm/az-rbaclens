"""Read when people holding powerful grants last signed in (plan "sign-ins").

The Activity Log never records Entra actions, so an Entra admin role nobody
uses looks like one used daily; the last sign-in is the next best signal. A
check reads, through Microsoft Graph, ``signInActivity`` and
``createdDateTime`` of each person holding a grant that can change who has
access or get keys and passwords, Entra or Azure, standing or eligible.

Optional, unlike group members and owners: ``signInActivity`` needs the
Graph permission AuditLog.Read.All and Entra ID P1/P2. A refusal, or Graph
not answering, leaves every date unknown and the check goes on. Someone
Graph no longer knows (404) is skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.tools.graph_groups import GRAPH_SCOPE, _build_ssl_context
from app.tools.graph_owners import GRAPH, _Gone, _Graph, _Refused, _when
from app.tools.graph_resolver import GraphUnavailable

logger = logging.getLogger(__name__)

_FIELDS = "id,createdDateTime,signInActivity"


@dataclass(frozen=True)
class SignIn:
    principal_id: str
    last_sign_in_at: datetime | None
    last_non_interactive_at: datetime | None
    last_successful_at: datetime | None
    account_created_at: datetime | None


def read(graph: _Graph, user_ids: set[str]) -> list[SignIn] | None:
    """One :class:`SignIn` per person Graph answered for; ``None`` when Graph
    refused (the permission or the licence is missing)."""
    out: list[SignIn] = []
    for uid in sorted(user_ids):
        try:
            body = graph.get(f"{GRAPH}/users/{uid}", {"$select": _FIELDS})
        except _Refused:
            logger.info("sign-in activity refused (AuditLog.Read.All or P1/P2 missing)")
            return None
        except _Gone:
            continue
        activity = body.get("signInActivity") or {}
        out.append(
            SignIn(
                principal_id=uid,
                last_sign_in_at=_when(activity.get("lastSignInDateTime")),
                last_non_interactive_at=_when(activity.get("lastNonInteractiveSignInDateTime")),
                last_successful_at=_when(activity.get("lastSuccessfulSignInDateTime")),
                account_created_at=_when(body.get("createdDateTime")),
            )
        )
    return out


def collect(credential: Any, user_ids: set[str]) -> list[SignIn] | None:
    """Read with ``credential``; ``None`` (unknown) on a refusal or any failure."""
    if not user_ids:
        return []
    try:
        token = credential.get_token(GRAPH_SCOPE).token
        with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
            return read(_Graph(client, token), user_ids)
    except GraphUnavailable as exc:
        logger.warning("sign-in activity not read: %s", exc)
        return None
    except Exception:
        logger.warning("sign-in activity not read", exc_info=True)
        return None
