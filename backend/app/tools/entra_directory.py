"""Read who holds which Entra role, for the Entra ID place (core/entra_roles.py).

The daily check of the Tenant Root Group also reads, through Microsoft Graph:

- the role definitions (``roleManagement/directory/roleDefinitions``), with
  their ``rolePermissions``, cached in ``role_definitions`` like Azure's;
- the role assignments (``…/roleAssignments?$expand=principal``): the source
  of truth for who holds a role, standing or active, directly or as a group.
  The active instances leave out assignments to groups, so they only mark
  PIM activations (``assignmentType = Activated``) and their end;
- PIM eligibility (``…/roleEligibilitySchedules?$expand=principal``).

They become ``role_assignments`` and ``role_eligibilities`` rows at the
Entra ID place, so places, changes, findings and reviews work on them as on
Azure grants. The identity's Entra role Directory Readers is enough.

A role held by a group of another tenant (``principalOrganizationId``, a
partner's group through delegated admin access, GDAP) is typed
``ForeignGroup``, as Azure does: its members are in that tenant, out of
reach, so they are not asked for. PIM eligibility doesn't say which tenant
its holder is in: an eligible group is foreign when an assignment says so,
or when this tenant's directory doesn't know it (``/groups/{id}`` answers 404).

A refusal (403) leaves the place unread: its grants are unknown, not
absent. Graph not answering fails the check, as for groups
(``GraphUnavailable``), and the scheduler tries again.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.core.entra_roles import (
    ENTRA_BUILTIN,
    ENTRA_CUSTOM,
    ENTRA_LEVEL,
    ENTRA_PLACE,
    ENTRA_PLACE_NAME,
)
from app.core.timeutil import as_utc
from app.db.models import RoleAssignment, RoleEligibility
from app.tools.graph_groups import GRAPH_SCOPE, _build_ssl_context
from app.tools.graph_resolver import GraphUnavailable

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
ROLES = f"{GRAPH}/roleManagement/directory"
MAX_PAGES = 200
_TYPES = {"user": "User", "group": "Group", "serviceprincipal": "ServicePrincipal"}


class EntraNotReadable(Exception):
    """Graph refused the Entra roles (no Directory Readers, or similar)."""


@dataclass
class EntraRoles:
    assignments: list[RoleAssignment] = field(default_factory=list)
    eligibilities: list[RoleEligibility] = field(default_factory=list)
    # ``{guid: {name, role_type, description, permissions}}`` for upsert_role_definitions.
    role_definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Groups holding or eligible for a role: their members are read like Azure's.
    group_ids: set[str] = field(default_factory=set)


def hierarchy_node(unread: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {"name": ENTRA_PLACE_NAME, "level": ENTRA_LEVEL, "parent": None}
    if unread:
        node["unread"] = True
    return node


def tenant_of(token: str) -> str | None:
    """The tenant id (``tid``) of our own Graph token: the Tenant Root Group's id."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    tid = claims.get("tid")
    return str(tid).lower() if tid else None


def is_tenant_root(scope: str, scope_level: str, tenant_id: str | None) -> bool:
    return (
        scope_level == "mgmt_group"
        and tenant_id is not None
        and scope.rstrip("/").rsplit("/", 1)[-1].lower() == tenant_id
    )


def _role_path(role_id: str) -> str:
    return f"{ENTRA_PLACE}/roleDefinitions/{role_id.lower()}"


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _principal(p: dict[str, Any] | None) -> tuple[str, str | None]:
    p = p or {}
    kind = str(p.get("@odata.type") or "").rsplit(".", 1)[-1].lower()
    return _TYPES.get(kind, kind or "Unknown"), p.get("userPrincipalName") or p.get("displayName")


class _Graph:
    def __init__(self, client: httpx.Client, token: str):
        self.client, self.headers = client, {"Authorization": f"Bearer {token}"}

    def pages(self, url: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        next_url: str | None = url
        for _page in range(MAX_PAGES):
            if next_url is None:
                return out
            response = self._get(next_url, params)
            params = None  # the next link carries them
            body = response.json()
            out.extend(body.get("value") or [])
            link = body.get("@odata.nextLink")
            # The token goes with the request: follow a next page only on Graph's own address.
            next_url = link if link and str(link).startswith(f"{GRAPH}/") else None
        raise GraphUnavailable(f"Entra roles: more than {MAX_PAGES} pages")

    def found(self, url: str) -> bool:
        """Does ``url`` exist (200) or not (404)? Other answers as :meth:`_get`."""
        try:
            response = self.client.get(url, headers=self.headers)
        except httpx.HTTPError as exc:
            raise GraphUnavailable(
                f"Microsoft Graph did not answer for the Entra roles ({type(exc).__name__})"
            ) from exc
        if response.status_code == 404:
            return False
        if response.status_code in (401, 403):
            raise EntraNotReadable(f"HTTP {response.status_code}")
        if response.status_code != 200:
            raise GraphUnavailable(
                f"Microsoft Graph answered HTTP {response.status_code} for the Entra roles"
            )
        return True

    def _get(self, url: str, params: dict[str, str] | None) -> httpx.Response:
        for attempt in range(4):
            try:
                response = self.client.get(url, params=params, headers=self.headers)
            except httpx.HTTPError as exc:
                raise GraphUnavailable(
                    f"Microsoft Graph did not answer for the Entra roles ({type(exc).__name__})"
                ) from exc
            if response.status_code in (401, 403):
                raise EntraNotReadable(f"HTTP {response.status_code}")
            if response.status_code in (429, 503, 504) and attempt < 3:
                time.sleep(min(float(response.headers.get("Retry-After") or 2**attempt), 30.0))
                continue
            if response.status_code != 200:
                raise GraphUnavailable(
                    f"Microsoft Graph answered HTTP {response.status_code} for the Entra roles"
                )
            return response
        raise GraphUnavailable("Microsoft Graph kept asking to slow down for the Entra roles")


def read(graph: _Graph, tenant_id: str | None = None) -> EntraRoles:
    """Every Entra role definition, assignment and eligibility, as rows (no ``scan_id`` yet).
    ``tenant_id`` tells the tenant's own principals from another tenant's."""
    out = EntraRoles()
    names: dict[str, str] = {}
    for d in graph.pages(f"{ROLES}/roleDefinitions"):
        rid = str(d.get("id") or "").lower()
        if not rid:
            continue
        names[rid] = d.get("displayName") or rid
        if d.get("templateId"):
            names.setdefault(str(d["templateId"]).lower(), names[rid])
        out.role_definitions[rid] = {
            "name": d.get("displayName"),
            "role_type": ENTRA_BUILTIN if d.get("isBuiltIn") else ENTRA_CUSTOM,
            "description": d.get("description"),
            # Graph's shape, kept as is: core/entra_roles.py reads it.
            "permissions": d.get("rolePermissions") or [],
        }

    active: dict[tuple[str, str], dict[str, Any]] = {}
    for inst in graph.pages(f"{ROLES}/roleAssignmentScheduleInstances"):
        key = (
            str(inst.get("principalId") or "").lower(),
            str(inst.get("roleDefinitionId") or "").lower(),
        )
        active[key] = inst

    assignments = graph.pages(f"{ROLES}/roleAssignments", {"$expand": "principal"})
    eligible = graph.pages(f"{ROLES}/roleEligibilitySchedules", {"$expand": "principal"})

    unknown = {
        str(x.get("roleDefinitionId") or "").lower() for x in [*assignments, *eligible]
    } - names.keys()
    if unknown:
        # A few roles are missing from the definitions; the legacy list may still name them.
        for role in graph.pages(f"{GRAPH}/directoryRoles"):
            tid = str(role.get("roleTemplateId") or "").lower()
            if tid in unknown and role.get("displayName"):
                names[tid] = role["displayName"]

    # Keep the definitions of roles someone holds or is eligible for, as the
    # Azure side does: the role guide lists roles in use, not Entra's catalogue.
    held = {str(x.get("roleDefinitionId") or "").lower() for x in [*assignments, *eligible]}
    out.role_definitions = {rid: d for rid, d in out.role_definitions.items() if rid in held}

    def role_type(rid: str) -> str | None:
        info = out.role_definitions.get(rid)
        return info["role_type"] if info else None

    for a in assignments:
        pid, rid = (
            str(a.get("principalId") or "").lower(),
            str(a.get("roleDefinitionId") or "").lower(),
        )
        if not (pid and rid):
            continue
        ptype, pname = _principal(a.get("principal"))
        org = str(a.get("principalOrganizationId") or "").lower()
        foreign = bool(tenant_id and org and org != tenant_id)
        if foreign and ptype == "Group":
            ptype = "ForeignGroup"
        inst = active.get((pid, rid)) or {}
        activated = inst.get("assignmentType") == "Activated"
        out.assignments.append(
            RoleAssignment(
                scope=ENTRA_PLACE,
                scope_name=ENTRA_PLACE_NAME,
                scope_level=ENTRA_LEVEL,
                principal_id=pid,
                principal_name=pname,
                principal_type=ptype,
                role_name=names.get(rid),
                role_definition_id=_role_path(rid),
                role_type=role_type(rid),
                assignment_type="Activated" if activated else "Assigned",
                end_at=_when(inst.get("endDateTime")),
                created_on=_when(inst.get("startDateTime")),
                raw={
                    "id": a.get("id"),
                    "source": "entra",
                    "directoryScopeId": a.get("directoryScopeId"),
                    "principalOrganizationId": org or None,
                },
            )
        )
        if ptype == "Group":
            out.group_ids.add(pid)

    foreign_ids = {a.principal_id for a in out.assignments if a.principal_type == "ForeignGroup"}
    own_groups = {a.principal_id for a in out.assignments if a.principal_type == "Group"}
    looked_up: dict[str, bool] = {}

    def foreign_group(pid: str) -> bool:
        if pid in foreign_ids:
            return True
        if pid in own_groups:
            return False
        if pid not in looked_up:
            looked_up[pid] = not graph.found(f"{GRAPH}/groups/{pid}?$select=id")
        return looked_up[pid]

    for e in eligible:
        pid, rid = (
            str(e.get("principalId") or "").lower(),
            str(e.get("roleDefinitionId") or "").lower(),
        )
        if not (pid and rid):
            continue
        ptype, pname = _principal(e.get("principal"))
        if ptype == "Group" and foreign_group(pid):
            ptype = "ForeignGroup"
        info = e.get("scheduleInfo") or {}
        out.eligibilities.append(
            RoleEligibility(
                azure_id=str(e.get("id") or f"{pid}:{rid}")[:255],
                scope=ENTRA_PLACE,
                scope_name=ENTRA_PLACE_NAME,
                scope_level=ENTRA_LEVEL,
                principal_id=pid,
                principal_name=pname,
                principal_type=ptype,
                role_definition_id=_role_path(rid),
                role_name=names.get(rid),
                role_type=role_type(rid),
                member_type=e.get("memberType"),
                start_at=_when(info.get("startDateTime")),
                end_at=_when((info.get("expiration") or {}).get("endDateTime")),
                status=e.get("status"),
            )
        )
        if ptype == "Group":
            out.group_ids.add(pid)
    return out


def collect(credential: Any) -> EntraRoles:
    """Read the Entra roles with ``credential``. Raises :class:`EntraNotReadable`
    on a refusal, :class:`GraphUnavailable` when Graph doesn't answer."""
    try:
        token = credential.get_token(GRAPH_SCOPE).token
    except Exception as exc:
        raise GraphUnavailable(f"no token for Microsoft Graph ({type(exc).__name__})") from exc
    with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
        return read(_Graph(client, token), tenant_of(token))
