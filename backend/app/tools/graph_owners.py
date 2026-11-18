"""Read who owns the apps and groups that hold access (plan "owners").

An app's owner can add a secret to it and act as it; a group's owner decides
its members. A check reads, through Microsoft Graph:

- for each app (service principal) holding a grant: the owners of the
  service principal, and of its app registration when the registration is
  in this tenant (``appOwnerOrganizationId``). Managed identities have no
  owners and are skipped;
- for each group holding a grant: its owners.

The same two calls give each app's secrets and certificates (kind, key id,
start and end dates; the secret's ``hint`` is dropped, and Graph never
returns a value).

The identity's Entra role Directory Readers is enough. Graph not answering
fails the check (:class:`GraphUnavailable`), as for group members; a
refusal (403) leaves that object's owners unknown (no resolution row); an
object that no longer exists (404) has none.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.tools.graph_groups import GRAPH_SCOPE, _build_ssl_context
from app.tools.graph_resolver import GraphUnavailable

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
_OWNER_FIELDS = "id,displayName,userPrincipalName"
_TYPES = {"user": "User", "group": "Group", "serviceprincipal": "ServicePrincipal"}


@dataclass(frozen=True)
class Owner:
    object_id: str  # the app's service principal or the group (lowercased)
    object_kind: str  # app | group
    via: str  # service_principal | registration | group
    owner_id: str
    owner_type: str  # User | ServicePrincipal | Group | other
    owner_name: str | None


@dataclass(frozen=True)
class Credential:
    object_id: str  # the app's service principal (lowercased)
    via: str  # registration | service_principal
    kind: str  # secret | certificate
    key_id: str
    start_at: datetime | None
    end_at: datetime | None


@dataclass
class Owners:
    owners: list[Owner] = field(default_factory=list)
    # Objects whose owners Graph answered for (possibly none). An object
    # missing here had its owners refused: unknown, not absent.
    read: set[str] = field(default_factory=set)
    # Apps registered in another tenant: their registration's owners are there.
    elsewhere: set[str] = field(default_factory=set)
    # {service principal id: the app's client id (appId)}.
    app_ids: dict[str, str] = field(default_factory=dict)
    credentials: list[Credential] = field(default_factory=list)


class _Refused(Exception):
    pass


class _Gone(Exception):
    pass


class _Graph:
    def __init__(self, client: httpx.Client, token: str):
        self.client, self.headers = client, {"Authorization": f"Bearer {token}"}

    def get(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        for attempt in range(4):
            try:
                r = self.client.get(url, params=params, headers=self.headers)
            except httpx.HTTPError as exc:
                raise GraphUnavailable(
                    f"Microsoft Graph did not answer for owners ({type(exc).__name__})"
                ) from exc
            if r.status_code in (401, 403):
                raise _Refused
            if r.status_code == 404:
                raise _Gone
            if r.status_code in (429, 503, 504) and attempt < 3:
                time.sleep(min(float(r.headers.get("Retry-After") or 2**attempt), 30.0))
                continue
            if r.status_code != 200:
                raise GraphUnavailable(f"Microsoft Graph answered HTTP {r.status_code} for owners")
            return r.json()
        raise GraphUnavailable("Microsoft Graph kept asking to slow down for owners")

    def owners(self, url: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        body = self.get(url, {"$select": _OWNER_FIELDS})
        for _page in range(50):
            out.extend(body.get("value") or [])
            link = body.get("@odata.nextLink")
            if not link or not str(link).startswith(f"{GRAPH}/"):
                break
            body = self.get(link)
        return out


def _owner(object_id: str, kind: str, via: str, o: dict[str, Any]) -> Owner:
    t = str(o.get("@odata.type") or "").rsplit(".", 1)[-1].lower()
    return Owner(
        object_id=object_id,
        object_kind=kind,
        via=via,
        owner_id=str(o.get("id") or "").lower(),
        owner_type=_TYPES.get(t, t or "other"),
        owner_name=o.get("userPrincipalName") or o.get("displayName"),
    )


_CREDENTIAL_FIELDS = "passwordCredentials,keyCredentials"


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _credentials(sp_id: str, via: str, body: dict[str, Any]) -> list[Credential]:
    out = []
    for field_name, kind in (("passwordCredentials", "secret"), ("keyCredentials", "certificate")):
        for c in body.get(field_name) or []:
            if c.get("keyId"):
                out.append(
                    Credential(
                        object_id=sp_id,
                        via=via,
                        kind=kind,
                        key_id=str(c["keyId"]).lower(),
                        start_at=_when(c.get("startDateTime")),
                        end_at=_when(c.get("endDateTime")),
                    )
                )
    return out


def read(
    graph: _Graph,
    apps: dict[str, tuple[str | None, str | None]],
    groups: set[str],
    tenant_id: str | None,
) -> Owners:
    """``apps``: ``{service principal id: (type, owning tenant)}``, either may be
    unknown (then looked up). ``groups``: group ids."""
    out = Owners()
    for sp_id, (sp_type, owner_org) in sorted(apps.items()):
        if sp_type == "ManagedIdentity":
            continue
        try:
            sp = graph.get(
                f"{GRAPH}/servicePrincipals/{sp_id}",
                {
                    "$select": "id,appId,servicePrincipalType,appOwnerOrganizationId,"
                    + _CREDENTIAL_FIELDS
                },
            )
            if sp.get("servicePrincipalType") == "ManagedIdentity":
                continue
            creds = _credentials(sp_id, "service_principal", sp)
            if sp.get("appId"):
                out.app_ids[sp_id] = str(sp["appId"]).lower()
            found = [
                _owner(sp_id, "app", "service_principal", o)
                for o in graph.owners(f"{GRAPH}/servicePrincipals/{sp_id}/owners")
            ]
            org = str(sp.get("appOwnerOrganizationId") or owner_org or "").lower()
            if sp.get("appId") and tenant_id and org == tenant_id:
                try:
                    app = graph.get(
                        f"{GRAPH}/applications(appId='{sp['appId']}')",
                        {"$select": f"id,{_CREDENTIAL_FIELDS}"},
                    )
                    creds += _credentials(sp_id, "registration", app)
                    found += [
                        _owner(sp_id, "app", "registration", o)
                        for o in graph.owners(f"{GRAPH}/applications/{app['id']}/owners")
                    ]
                except _Gone:
                    out.elsewhere.add(sp_id)
            elif sp.get("appId"):
                out.elsewhere.add(sp_id)
        except _Refused:
            logger.info("owners of app %s refused: left unknown", sp_id)
            continue
        except _Gone:
            out.read.add(sp_id)  # gone since: no owners to show
            continue
        out.owners.extend(o for o in found if o.owner_id)
        out.credentials.extend(creds)
        out.read.add(sp_id)
    for group_id in sorted(groups):
        try:
            found = [
                _owner(group_id, "group", "group", o)
                for o in graph.owners(f"{GRAPH}/groups/{group_id}/owners")
            ]
        except _Refused:
            logger.info("owners of group %s refused: left unknown", group_id)
            continue
        except _Gone:
            out.read.add(group_id)
            continue
        out.owners.extend(o for o in found if o.owner_id)
        out.read.add(group_id)
    return out


def collect(
    credential: Any,
    apps: dict[str, tuple[str | None, str | None]],
    groups: set[str],
    tenant_id: str | None,
) -> Owners:
    """Read the owners with ``credential``. Raises :class:`GraphUnavailable`."""
    if not apps and not groups:
        return Owners()
    try:
        token = credential.get_token(GRAPH_SCOPE).token
    except Exception as exc:
        raise GraphUnavailable(f"no token for Microsoft Graph ({type(exc).__name__})") from exc
    with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
        return read(_Graph(client, token), apps, groups, tenant_id)
