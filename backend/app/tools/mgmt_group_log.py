"""Read the Activity Log of management groups.

The Azure SDK reads a subscription's Activity Log; a management group's is
only on the REST API (``.../managementGroups/<id>/providers/
microsoft.insights/eventtypes/management/values``). It holds what was done
on the group itself, not in the subscriptions under it. Entries come back
in the same shape as a subscription's, so they are turned into the SDK's
``EventData`` and stored by the same code (``tools/activity.py``).

Azure answers 403 both for a group the identity may not read and for one
that does not exist (any more): :class:`GroupNotReadable` covers both.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from azure.core import PipelineClient
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
from azure.core.rest import HttpRequest
from azure.mgmt.monitor.models import EventData
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import Scope

ARM = "https://management.azure.com"
API_VERSION = "2017-03-01-preview"
# A group seen by a check this long before the newest sighting of any group
# is taken as gone: checks run daily.
SEEN_WITHIN = timedelta(days=2)
MAX_PAGES = 500
_GROUP_ID = re.compile(r"^[A-Za-z0-9_().-]{1,90}$")


class GroupNotReadable(Exception):
    """Azure refused the group's log: not allowed, or the group is gone."""


def groups_to_read(session: Session) -> list[tuple[str, str]]:
    """``(id, name)`` of the management groups the newest checks saw, by name."""
    newest = session.scalar(select(func.max(Scope.last_seen_at)).where(Scope.level == "mgmt_group"))
    if newest is None:
        return []
    newest = as_utc(newest)
    out: list[tuple[str, str]] = []
    for scope_id, name, seen in session.execute(
        select(Scope.id, Scope.name, Scope.last_seen_at).where(Scope.level == "mgmt_group")
    ).all():
        seen = as_utc(seen)
        group = scope_id.rsplit("/", 1)[-1]
        if seen >= newest - SEEN_WITHIN and _GROUP_ID.match(group):
            out.append((group, name or group))
    return sorted(out, key=lambda g: g[1].lower())


def client(credential: Any, transport: Any = None) -> PipelineClient:
    return PipelineClient(
        ARM,
        policies=[RetryPolicy(), BearerTokenCredentialPolicy(credential, f"{ARM}/.default")],
        transport=transport,
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def entries(arm: PipelineClient, group: str, start: datetime, end: datetime) -> Iterator[EventData]:
    """Every entry of ``group``'s log in the window. Raises :class:`GroupNotReadable`
    on a refusal, ``RuntimeError`` on any other answer than 200."""
    if not _GROUP_ID.match(group):
        raise ValueError(f"not a management group id: {group!r}")
    url = (
        f"{ARM}/providers/Microsoft.Management/managementGroups/{quote(group, safe='')}"
        "/providers/microsoft.insights/eventtypes/management/values"
    )
    request = HttpRequest(
        "GET",
        url,
        params={
            "api-version": API_VERSION,
            "$filter": f"eventTimestamp ge '{_iso(start)}' and eventTimestamp le '{_iso(end)}'",
        },
    )
    for _page in range(MAX_PAGES):
        response = arm.send_request(request)
        if response.status_code in (403, 404):
            raise GroupNotReadable(group)
        if response.status_code != 200:
            raise RuntimeError(
                f"Activity Log of management group {group}: HTTP {response.status_code} {response.text()[:300]}"
            )
        body = response.json()
        for item in body.get("value") or []:
            yield EventData.from_dict(item)
        next_link = body.get("nextLink")
        # The token goes with the request: follow a next page only on Azure's own address.
        if not next_link or not str(next_link).startswith(f"{ARM}/"):
            return
        request = HttpRequest("GET", next_link)
    raise RuntimeError(f"Activity Log of management group {group}: more than {MAX_PAGES} pages")
