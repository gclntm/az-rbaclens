"""Read who changed group membership and Entra roles, from the Entra ID audit log.

Adding someone to a group, or giving them an Entra role, is a change in
Entra ID, not in Azure: the Activity Log never sees it, so the changes feed
could only say when a check first saw it. Entra records it in its audit
log, read here through Microsoft Graph (``/auditLogs/directoryAudits``).
Only the entries in :data:`ACTIVITIES` are kept, in ``directory_audit_events``:
group members added or removed, Entra roles given or removed (directly or
through PIM), eligibility added or removed, and PIM activations.
``core/changes.py`` matches them to the differences a check saw.

Optional. The identity needs the Graph application permission
``AuditLog.Read.All`` (read-only). Without it RBAC Lens works as before: the
audit log is tried once a day (:func:`refresh_status_if_due`), and the
hourly collection is only added once a read succeeds
(``core/default_schedules.py``). Entra keeps the log 7 days (Entra ID Free)
or 30 days (P1/P2), so the first collection reads at most that.

The collection reuses the Activity Log collection job, with the target
``entra-directory`` (``core/activity_targets.py``).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.core import PipelineClient
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, RetryPolicy
from azure.core.rest import HttpRequest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import ActivityPull, AppSetting, DirectoryAuditEvent

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com"
AUDITS_URL = f"{GRAPH}/v1.0/auditLogs/directoryAudits"
# activityDisplayName → (kind, action). Kinds: group (its members), role (an Entra
# role given or removed), eligible (through PIM), activation (a PIM activation:
# use of an eligibility, not a change of who has access).
ACTIVITIES: dict[str, tuple[str, str]] = {
    "Add member to group": ("group", "add"),
    "Remove member from group": ("group", "remove"),
    "Add member to role": ("role", "add"),
    "Add member to role in PIM completed (permanent)": ("role", "add"),
    "Add member to role in PIM completed (timebound)": ("role", "add"),
    "Add member to role outside of PIM (permanent)": ("role", "add"),
    "Remove member from role": ("role", "remove"),
    "Remove member from role in PIM completed (permanent)": ("role", "remove"),
    "Remove member from role in PIM completed (timebound)": ("role", "remove"),
    "Remove permanent direct role assignment": ("role", "remove"),
    "Add eligible member to role": ("eligible", "add"),
    "Add eligible member to role in PIM completed (permanent)": ("eligible", "add"),
    "Add eligible member to role in PIM completed (timebound)": ("eligible", "add"),
    "Remove eligible member from role": ("eligible", "remove"),
    "Remove eligible member from role in PIM completed (permanent)": ("eligible", "remove"),
    "Remove eligible member from role in PIM completed (timebound)": ("eligible", "remove"),
    "Remove permanent eligible role assignment": ("eligible", "remove"),
    "Add member to role completed (PIM activation)": ("activation", "add"),
    "Remove member from role (PIM activation expired)": ("activation", "remove"),
}
CATEGORIES = ("GroupManagement", "RoleManagement")
_MEMBER_TYPES = {"User", "Group", "ServicePrincipal"}
MAX_PAGES = 2000
BATCH_SIZE = 200
HEARTBEAT_EVERY_SECONDS = 20.0

STATUS_KEY = "entra_audit"
# readable | no_permission | licence_required | unavailable (could not tell: retried)
READABLE = "readable"
NO_PERMISSION = "no_permission"
LICENCE_REQUIRED = "licence_required"
UNAVAILABLE = "unavailable"
# How often a refused audit log is tried again.
RETRY_REFUSED_AFTER = timedelta(hours=20)

_PERMISSION_CODES = {"authentication_msgraphpermissionmissing", "authorization_requestdenied"}
_LICENCE_CODES = {"authentication_requestfromnonpremiumtenantorb2ctenant"}
_MESSAGES = {
    NO_PERMISSION: (
        "The identity lacks the Microsoft Graph application permission AuditLog.Read.All "
        "(read-only), so the Entra ID audit log can't be read."
    ),
    LICENCE_REQUIRED: (
        "Microsoft Graph refused the audit log because of the tenant's licence "
        "(reading it through the API may need Entra ID P1 or P2)."
    ),
}


class AuditLogNotReadable(Exception):
    """Graph refused the audit log: ``reason`` is NO_PERMISSION or LICENCE_REQUIRED."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(_MESSAGES[reason] + (f" ({detail})" if detail else ""))


def client(credential: Any, transport: Any = None) -> PipelineClient:
    return PipelineClient(
        GRAPH,
        policies=[RetryPolicy(), BearerTokenCredentialPolicy(credential, f"{GRAPH}/.default")],
        transport=transport,
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error_code(response: Any) -> str:
    try:
        return str(((response.json() or {}).get("error") or {}).get("code") or "")
    except (ValueError, AttributeError):
        return ""


def _raise_if_refused(response: Any) -> None:
    if response.status_code not in (401, 403):
        return
    code = _error_code(response)
    if code.lower() in _LICENCE_CODES:
        raise AuditLogNotReadable(LICENCE_REQUIRED, code)
    raise AuditLogNotReadable(NO_PERMISSION, code or f"HTTP {response.status_code}")


def _window_filter(start: datetime, end: datetime) -> str:
    return f"activityDateTime ge {_iso(start)} and activityDateTime le {_iso(end)}"


# Graph refuses a window that starts before what Entra keeps (7 or 30 days) with
# HTTP 400 "Minimum allowed time for activityDateTime is 10/18/2026 12:00:00 AM".
_MINIMUM_TIME = re.compile(
    r"Minimum allowed time for activityDateTime is (\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)"
)


def _minimum_time(response: Any) -> datetime | None:
    """The earliest time Graph accepts, from its 400 answer, or ``None``."""
    found = _MINIMUM_TIME.search(response.text() or "")
    if not found:
        return None
    return datetime.strptime(found.group(1), "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=UTC)


def entries(graph: PipelineClient, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
    """The window's entries listed in :data:`ACTIVITIES`, as Graph returns them.

    Asks Graph for the group and role categories only, and keeps the listed
    activities. A window starting before what Entra keeps is moved to the
    earliest time Graph accepts. If Graph rejects the filter itself (``or``
    is not documented for this API), asks for the window alone. Raises
    :class:`AuditLogNotReadable` on a refusal, ``RuntimeError`` on any other
    answer than 200.
    """
    names = " or ".join(f"category eq '{c}'" for c in CATEGORIES)

    def first_request(narrow: bool) -> HttpRequest:
        window = _window_filter(start, end)
        return HttpRequest(
            "GET", AUDITS_URL, params={"$filter": f"{window} and ({names})" if narrow else window}
        )

    request, narrow, first = first_request(True), True, True
    for _page in range(MAX_PAGES):
        response = graph.send_request(request)
        _raise_if_refused(response)
        if first and response.status_code == 400 and (floor := _minimum_time(response)):
            if floor + timedelta(minutes=1) >= end:
                return  # nothing Entra still keeps in this window
            if floor + timedelta(minutes=1) > start:
                logger.info("Entra keeps its audit log from %s: window starts there", floor)
                start = floor + timedelta(minutes=1)
                request = first_request(narrow)
                continue
        if first and narrow and response.status_code == 400:
            logger.info("directoryAudits refused the activity filter; filtering here")
            narrow = False
            request = first_request(narrow)
            continue
        first = False
        if response.status_code != 200:
            raise RuntimeError(
                f"Entra ID audit log: HTTP {response.status_code} {response.text()[:300]}"
            )
        body = response.json()
        for item in body.get("value") or []:
            if item.get("activityDisplayName") in ACTIVITIES:
                yield item
        next_link = body.get("@odata.nextLink")
        # The token goes with the request: follow a next page only on Graph's own address.
        if not next_link or not str(next_link).startswith(f"{GRAPH}/"):
            return
        request = HttpRequest("GET", next_link)
    raise RuntimeError(f"Entra ID audit log: more than {MAX_PAGES} pages")


def _clean(value: Any) -> str | None:
    """Graph's modifiedProperties values are JSON strings (``"\\"<id>\\""``)."""
    if value in (None, ""):
        return None
    text = str(value)
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = text
    if isinstance(decoded, list):
        decoded = decoded[0] if decoded else None
    return str(decoded).strip() or None if decoded is not None else None


def _group_from_properties(targets: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """``Group.ObjectID`` / ``Group.DisplayName`` from the member's modified properties."""
    gid = name = None
    for t in targets:
        for p in t.get("modifiedProperties") or []:
            key = p.get("displayName")
            value = _clean(p.get("newValue")) or _clean(p.get("oldValue"))
            if key == "Group.ObjectID" and value:
                gid = value
            elif key == "Group.DisplayName" and value:
                name = value
    return gid, name


def _type(target: dict[str, Any]) -> str | None:
    t = target.get("type") or target.get("Type")
    return {"user": "User", "group": "Group", "serviceprincipal": "ServicePrincipal"}.get(
        str(t or "").lower(), t
    )


def _name(target: dict[str, Any]) -> str | None:
    return target.get("userPrincipalName") or target.get("displayName") or None


def _role_from(targets: list[dict[str, Any]]) -> str | None:
    """The role's template id: ``Role.TemplateId`` on the member (Core Directory),
    or ``TemplateId`` / the id of the target typed Role (PIM)."""
    for t in targets:
        for p in t.get("modifiedProperties") or []:
            if p.get("displayName") in ("Role.TemplateId", "TemplateId"):
                value = _clean(p.get("newValue")) or _clean(p.get("oldValue"))
                if value:
                    return value.lower()
    role = next(
        (t for t in targets if str(t.get("type") or t.get("Type") or "").lower() == "role"), None
    )
    return str(role["id"]).lower() if role else None


def _initiator(item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    initiated = item.get("initiatedBy") or {}
    user, app = initiated.get("user") or {}, initiated.get("app") or {}
    if user.get("id"):
        return "user", user["id"], user.get("userPrincipalName") or user.get("displayName")
    if app.get("servicePrincipalId") or app.get("appId") or app.get("displayName"):
        return (
            "app",
            app.get("servicePrincipalId") or app.get("appId"),
            app.get("displayName") or app.get("servicePrincipalName"),
        )
    return None, None, None


def _when(item: dict[str, Any]) -> datetime:
    return as_utc(datetime.fromisoformat(str(item["activityDateTime"]).replace("Z", "+00:00")))


def _parse_role(
    item: dict[str, Any], kind: str, action: str, targets: list
) -> DirectoryAuditEvent | None:
    role_id = _role_from(targets)
    member = next((t for t in targets if _type(t) in _MEMBER_TYPES), None)
    if not (role_id and member):
        return None
    ikind, iid, iname = _initiator(item)
    return DirectoryAuditEvent(
        audit_id=str(item["id"])[:100],
        kind=kind,
        action=action,
        occurred_at=_when(item),
        result=(item.get("result") or None),
        role_id=role_id,
        member_id=member["id"].lower(),
        member_type=_type(member),
        member_name=_name(member),
        initiator_type=ikind,
        initiator_id=iid.lower() if iid else None,
        initiator_name=iname,
        raw=item,
    )


def parse(item: dict[str, Any]) -> DirectoryAuditEvent | None:
    """One audit entry as a row, or ``None`` when it doesn't name what changed for whom."""
    kind, action = ACTIVITIES.get(item.get("activityDisplayName") or "", (None, None))
    audit_id = item.get("id")
    when = item.get("activityDateTime")
    targets = [t for t in item.get("targetResources") or [] if t.get("id")]
    if not (kind and action and audit_id and when and targets):
        return None
    if kind != "group":
        return _parse_role(item, kind, action, targets)
    gid, gname = _group_from_properties(targets)
    group = next((t for t in targets if gid and t["id"].lower() == gid.lower()), None)
    if gid is None:
        # No Group.ObjectID (Microsoft's own example has none): the group is
        # the one target typed Group. A group added to a group gives two, and
        # which one is the parent can't be told: skip it rather than guess.
        groups = [t for t in targets if _type(t) == "Group"]
        if len(groups) != 1 or len(targets) != 2:
            return None
        group = groups[0]
        gid = group["id"]
    member = next((t for t in targets if gid and t["id"].lower() != gid.lower()), None)
    if gid is None or member is None:
        return None
    ikind, iid, iname = _initiator(item)
    return DirectoryAuditEvent(
        audit_id=str(audit_id)[:100],
        kind="group",
        action=action,
        occurred_at=_when(item),
        result=(item.get("result") or None),
        group_id=gid.lower(),
        group_name=gname or (_name(group) if group else None),
        member_id=member["id"].lower(),
        member_type=_type(member),
        member_name=_name(member),
        initiator_type=ikind,
        initiator_id=iid.lower() if iid else None,
        initiator_name=iname,
        raw=item,
    )


def collect(session: Session, graph: PipelineClient, pull: ActivityPull) -> tuple[int, int]:
    """Store the window's membership entries not on file yet: ``(persisted, seen)``."""
    seen = persisted = 0
    batch: list[DirectoryAuditEvent] = []
    beat_at = time.monotonic()

    def flush() -> None:
        nonlocal persisted, batch
        ids = [r.audit_id for r in batch]
        known = set(
            session.scalars(
                select(DirectoryAuditEvent.audit_id).where(DirectoryAuditEvent.audit_id.in_(ids))
            ).all()
        )
        fresh = {r.audit_id: r for r in batch if r.audit_id not in known}
        session.add_all(fresh.values())
        persisted += len(fresh)
        batch = []
        pull.progress_done = persisted
        pull.progress_message = f"Read {seen} audit entries, {persisted} new…"
        pull.heartbeat_at = datetime.now(UTC)
        session.commit()

    for item in entries(graph, pull.start_time, pull.end_time):
        seen += 1
        row = parse(item)
        if row is not None:
            batch.append(row)
        if len(batch) >= BATCH_SIZE or time.monotonic() - beat_at >= HEARTBEAT_EVERY_SECONDS:
            flush()
            beat_at = time.monotonic()
    flush()
    return persisted, seen


# ---- can the audit log be read? -------------------------------------------------------


def probe(graph: PipelineClient, now: datetime) -> str:
    """READABLE, NO_PERMISSION or LICENCE_REQUIRED, from one small read."""
    request = HttpRequest(
        "GET",
        AUDITS_URL,
        params={"$filter": _window_filter(now - timedelta(hours=1), now), "$top": "1"},
    )
    response = graph.send_request(request)
    try:
        _raise_if_refused(response)
    except AuditLogNotReadable as exc:
        return exc.reason
    if response.status_code != 200:
        raise RuntimeError(f"Entra ID audit log: HTTP {response.status_code}")
    return READABLE


def read_status(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, STATUS_KEY)
    return dict(row.value) if row else {}


def save_status(session: Session, status: str, now: datetime, message: str | None = None) -> None:
    """Remember whether the audit log can be read. The caller commits."""
    value = {
        "status": status,
        "checked_at": now.isoformat(),
        "message": message if message is not None else _MESSAGES.get(status),
    }
    row = session.get(AppSetting, STATUS_KEY)
    if row is None:
        session.add(AppSetting(key=STATUS_KEY, value=value))
    else:
        row.value = value


def _unavailable_message(exc: Exception) -> str:
    if type(exc).__name__ == "ClientAuthenticationError":
        return (
            "Could not sign in to Microsoft Graph: check the Azure identity "
            "(Settings → Credentials, or ARM_* in the env file)."
        )
    return f"Could not reach Microsoft Graph ({type(exc).__name__})."


def refresh_status_if_due(
    session: Session, now: datetime, graph: PipelineClient | None = None, force: bool = False
) -> str | None:
    """Try the audit log when its state is unknown or a refusal is old enough.

    Once readable, the collections themselves keep the state current. A
    failure to tell (network, Graph down) is recorded as UNAVAILABLE and
    tried again next time. Commits. Returns the state, or ``None`` when not due.
    """
    state = read_status(session)
    checked = state.get("checked_at")
    if not force and state.get("status") == READABLE:
        return None
    if (
        not force
        and checked
        and state.get("status") != UNAVAILABLE
        and now - as_utc(datetime.fromisoformat(checked)) < RETRY_REFUSED_AFTER
    ):
        return None
    if graph is None:
        from app.core.azure import get_azure_transport, get_credential

        graph = client(get_credential(), get_azure_transport())
    try:
        status = probe(graph, now)
        save_status(session, status, now)
    except Exception as exc:
        logger.warning("Entra ID audit log: could not tell whether it can be read", exc_info=True)
        status = UNAVAILABLE
        save_status(session, status, now, _unavailable_message(exc))
    session.commit()
    if status != READABLE:
        logger.info("Entra ID audit log not readable: %s", status)
    return status
