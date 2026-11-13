"""Microsoft Graph resolver for group transitive membership.

For each group ID we ask Graph for ``/groups/{id}/transitiveMembers``
which flattens nested groups, mixed user / SP / group members, etc.
Returns ``{group_id: [GroupMember]}`` — best-effort, falls back to
empty list per group on permission failure so a single problematic
group doesn't kill the whole scan.

Different shape from ``graph_resolver.resolve_principals``: that one
batches one call across all principals to ``getByIds``. Here Graph
doesn't offer a batched flavour of ``transitiveMembers``, so we issue
one HTTP per group. For typical scans that's a few dozen groups and
runs in seconds.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass
from typing import Any

import httpx
from azure.core.credentials import TokenCredential

from app.core.azure import ca_bundle_path
from app.tools.graph_resolver import GraphUnavailable

logger = logging.getLogger(__name__)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_GROUP_MEMBERS_URL = "https://graph.microsoft.com/v1.0/groups/{group_id}/transitiveMembers"
# Page size big enough for most security groups. Graph paginates above
# this with @odata.nextLink which we follow until exhaustion.
PAGE_SIZE = 999


@dataclass(frozen=True)
class GroupMember:
    """One entry in a group's transitive member list."""

    id: str
    type: str | None  # User | Group | ServicePrincipal | Device | None
    name: str | None  # UPN for users; displayName otherwise


# Microsoft Graph's ``@odata.type`` strings → our shorter type names,
# matching ``role_assignments.principal_type`` so callers can compare
# without re-mapping.
_ODATA_TO_TYPE = {
    "#microsoft.graph.user": "User",
    "#microsoft.graph.group": "Group",
    "#microsoft.graph.servicePrincipal": "ServicePrincipal",
    "#microsoft.graph.device": "Device",
}


def resolve_group_memberships(
    group_ids: list[str], credential: TokenCredential, *, strict: bool = False
) -> dict[str, list[GroupMember] | None]:
    """Return ``{group_id: members}`` for every group we tried to fetch.

    Two outcomes encoded in the value:

    - ``list[GroupMember]`` (possibly empty) — Graph returned a valid
      response. An empty list means the group genuinely has no
      members.
    - ``None`` — Graph refused (403/404), the call errored, or we
      couldn't get a token at all. Distinguishes "we don't know"
      from "we know there are zero".

    ``strict``: raise ``GraphUnavailable`` when Graph can't be reached
    (anything but a 403/404 answer), instead of recording "unknown".
    """
    if not group_ids:
        return {}

    try:
        token = credential.get_token(GRAPH_SCOPE).token
    except Exception as exc:
        if strict:
            raise GraphUnavailable(f"no token for Microsoft Graph ({type(exc).__name__})") from exc
        logger.warning(
            "could not acquire Graph token; group memberships will be missing",
            exc_info=True,
        )
        return dict.fromkeys(group_ids)

    headers = {"Authorization": f"Bearer {token}"}
    out: dict[str, list[GroupMember] | None] = {}

    with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
        for group_id in group_ids:
            try:
                out[group_id] = _fetch_members(client, headers, group_id)
            except Exception as exc:
                if strict:
                    raise GraphUnavailable(
                        f"Microsoft Graph did not answer for the members of a group ({type(exc).__name__})"
                    ) from exc
                # Don't let one bad group take down the rest.
                logger.exception("transitiveMembers failed for group %s; skipping", group_id)
                out[group_id] = None
    return out


def _fetch_members(
    client: httpx.Client, headers: dict[str, str], group_id: str
) -> list[GroupMember] | None:
    """Walk ``@odata.nextLink`` pages until exhaustion.

    Returns ``None`` on permission failure / deleted group so the
    caller can mark this group as "unresolved" instead of
    "empty". A 200 OK with zero members returns ``[]``.
    """
    url: str | None = GRAPH_GROUP_MEMBERS_URL.format(group_id=group_id)
    params: dict[str, str] | None = {
        # Only fetch the fields we need. ``id`` is always present;
        # ``userPrincipalName`` is user-only and silently dropped for
        # other types — Graph returns null instead of erroring.
        "$select": "id,displayName,userPrincipalName",
        "$top": str(PAGE_SIZE),
    }
    members: list[GroupMember] = []
    while url:
        resp = client.get(url, headers=headers, params=params)
        if resp.status_code in (403, 404):
            logger.info(
                "graph transitiveMembers %s → %d (insufficient permission or deleted)",
                group_id,
                resp.status_code,
            )
            return None
        resp.raise_for_status()
        body = resp.json()
        for entry in body.get("value", []) or []:
            members.append(_to_member(entry))
        url = body.get("@odata.nextLink")
        # Once nextLink is followed it already carries the params, so
        # we must not re-add them.
        params = None
    return members


def _to_member(entry: dict[str, Any]) -> GroupMember:
    odata = entry.get("@odata.type") or ""
    member_type = _ODATA_TO_TYPE.get(odata)
    member_id = (entry.get("id") or "").strip()
    # Users: prefer UPN; falls back to displayName when UPN is empty
    # (e.g. external guests).
    upn = entry.get("userPrincipalName")
    display = entry.get("displayName")
    if member_type == "User" and isinstance(upn, str) and upn:
        name: str | None = upn
    elif isinstance(display, str) and display:
        name = display
    else:
        name = None
    return GroupMember(id=member_id.lower(), type=member_type, name=name)


def _build_ssl_context() -> ssl.SSLContext:
    """Same approach as graph_resolver: honour the corporate CA bundle
    when ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE`` is set."""
    ca_file = ca_bundle_path()
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context()
