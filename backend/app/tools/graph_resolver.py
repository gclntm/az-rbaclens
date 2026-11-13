"""Microsoft Graph resolver for principal IDs.

Maps Azure AD object IDs (users, groups, service principals) to display
names via the Graph ``/v1.0/directoryObjects/getByIds`` endpoint.

Resolution is grouped by principal type so that a missing permission
(e.g. ``Group.Read.All`` granted but not ``Application.Read.All``) only
loses one type instead of silently dropping rows. Each batch failure is
logged independently.

Best-effort: returns an empty / partial dict if the credential lacks
the relevant Graph application permissions or the call fails. Callers
should fall back to the raw principal ID.
"""

from __future__ import annotations

import logging
import re
import ssl
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from azure.core.credentials import TokenCredential

from app.core.azure import ca_bundle_path

logger = logging.getLogger(__name__)


class GraphUnavailable(RuntimeError):
    """Microsoft Graph did not answer (network, throttling, a server error).

    Raised only by ``strict`` lookups: a check must fail rather than go on
    with names or members missing. A refusal (401/403: the app lacks the
    permission) is a stable fact and never raises.
    """


def _is_refusal(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)


GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BATCH_URL = "https://graph.microsoft.com/v1.0/directoryObjects/getByIds"
BATCH_SIZE = 1000  # Graph max per call

# Tenants Microsoft publishes its own apps from (``appOwnerOrganizationId``):
# the first-party service tenant, and Microsoft's corporate tenant.
MICROSOFT_TENANTS = frozenset(
    {
        "f8cdef31-a31e-4b4a-93e4-5f571e91255a",
        "72f988bf-86f1-41af-91ab-2d7cd011db47",
    }
)

# ``{object id: (appOwnerOrganizationId, servicePrincipalType)}`` for apps.
AppInfo = dict[str, tuple[str | None, str | None]]


def is_microsoft_app(app_owner_org: str | None) -> bool:
    """True for a service principal of an app Microsoft publishes (Defender, AKS, …)."""
    return (app_owner_org or "").lower() in MICROSOFT_TENANTS


# Map Azure RBAC ``principalType`` → Graph ``getByIds`` ``types`` value.
_RBAC_TO_GRAPH_TYPE = {
    "User": "user",
    "Group": "group",
    "ServicePrincipal": "servicePrincipal",
}


def resolve_principals(
    principals: Mapping[str, str],
    credential: TokenCredential,
    apps_out: AppInfo | None = None,
    *,
    strict: bool = False,
) -> dict[str, str]:
    """Resolve ``{principal_id: principal_type}`` to display names.

    Returns ``{}`` (or a partial map) on failure. For users, prefers
    ``userPrincipalName`` over ``displayName`` (UPNs are more useful
    in an access-review context). Groups and service principals fall
    back to ``displayName``. ``apps_out``, when given, receives each
    service principal's owning tenant and kind (see ``AppInfo``).

    ``strict``: raise :class:`GraphUnavailable` when Graph can't be reached,
    instead of returning fewer names.
    """
    if not principals:
        return {}

    try:
        token = credential.get_token(GRAPH_SCOPE).token
    except Exception as exc:
        if strict:
            raise GraphUnavailable(f"no token for Microsoft Graph ({type(exc).__name__})") from exc
        logger.warning(
            "could not acquire Graph token; principal names will be missing",
            exc_info=True,
        )
        return {}

    # Group IDs by Graph type. Anything we don't recognise (ForeignGroup,
    # MSI, …) goes into an untyped bucket and is queried without ``types``.
    by_type: dict[str, set[str]] = defaultdict(set)
    untyped: set[str] = set()
    for pid, rbac_type in principals.items():
        if not pid:
            continue
        graph_type = _RBAC_TO_GRAPH_TYPE.get(rbac_type)
        if graph_type:
            by_type[graph_type].add(pid)
        else:
            untyped.add(pid)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    out: dict[str, str] = {}

    with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
        for graph_type, ids_set in by_type.items():
            _query_batch(
                client,
                headers,
                ids=sorted(ids_set),
                graph_type=graph_type,
                out=out,
                apps_out=apps_out,
                strict=strict,
            )
        if untyped:
            _query_batch(
                client,
                headers,
                ids=sorted(untyped),
                graph_type=None,
                out=out,
                apps_out=apps_out,
                strict=strict,
            )

    missing = set(principals) - set(out)
    if missing:
        logger.info(
            "Graph resolved %d/%d principals (%d unresolved)",
            len(out),
            len(principals),
            len(missing),
        )
    return out


# Graph ``@odata.type`` → Azure RBAC ``principalType``.
_GRAPH_TO_RBAC_TYPE = {
    "#microsoft.graph.user": "User",
    "#microsoft.graph.group": "Group",
    "#microsoft.graph.servicePrincipal": "ServicePrincipal",
}


def resolve_objects(
    ids: set[str], credential: TokenCredential
) -> dict[str, tuple[str, str | None]]:
    """Resolve object ids of unknown type to ``{id: (name, RBAC type)}``.

    For identities seen outside role assignments, such as who created a
    grant. One untyped ``getByIds`` call per batch; best effort like
    ``resolve_principals``.
    """
    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        token = credential.get_token(GRAPH_SCOPE).token
    except Exception:
        logger.warning(
            "could not acquire Graph token; creator names will be missing", exc_info=True
        )
        return {}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    names: dict[str, str] = {}
    types: dict[str, str | None] = {}
    with httpx.Client(timeout=30.0, verify=_build_ssl_context()) as client:
        _query_batch(client, headers, ids=sorted(ids), graph_type=None, out=names, types_out=types)
    return {pid: (name, types.get(pid)) for pid, name in names.items()}


GRAPH_USERS_URL = "https://graph.microsoft.com/v1.0/users"
_EMAIL = re.compile(r"^[^@\s'\"]+@[^@\s'\"]+\.[^@\s'\"]+$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class EntraUser:
    id: str
    display_name: str | None
    email: str | None


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL.match(value.strip()))


def find_user(value: str, credential: TokenCredential, *, strict: bool = False) -> EntraUser | None:
    """One Entra ID user by email, user principal name or object id; ``None``
    when there is no such user, it is not a user, or Graph can't be reached.
    With ``strict``, failing to reach Graph raises instead of answering ``None``,
    so a caller can retry later rather than conclude "no such user".

    Input is validated before it reaches the ``$filter`` (no quotes, one @),
    so it cannot change the query.
    """
    value = value.strip()
    if _GUID.match(value):
        url, params = f"{GRAPH_USERS_URL}/{value.lower()}", None
    elif looks_like_email(value):
        url = GRAPH_USERS_URL
        params = {
            "$filter": f"mail eq '{value}' or userPrincipalName eq '{value}'",
            "$select": "id,displayName,mail,userPrincipalName",
        }
    else:
        return None
    try:
        token = credential.get_token(GRAPH_SCOPE).token
        with httpx.Client(timeout=15.0, verify=_build_ssl_context()) as client:
            response = client.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning("Graph user lookup failed: %s", type(exc).__name__)
        if strict:
            raise
        return None
    found = payload.get("value", [payload]) if params else [payload]
    if len(found) != 1 or not found[0].get("id"):
        return None
    u = found[0]
    return EntraUser(
        u["id"].lower(), u.get("displayName"), u.get("mail") or u.get("userPrincipalName")
    )


def _build_ssl_context() -> ssl.SSLContext:
    """SSL context that honors the shared CA bundle from ``app.core.azure``.

    httpx defaults to the certifi bundle and ignores ``REQUESTS_CA_BUNDLE`` /
    ``SSL_CERT_FILE``. In corporate environments where TLS to Graph is
    intercepted by a proxy with a self-signed root CA, ``ca_bundle_path()``
    returns the user-provided bundle so we can seed the context with it.
    Otherwise we fall back to the OS trust store, which picks up
    system-installed CAs.
    """
    ca_file = ca_bundle_path()
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context()


def _query_batch(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    ids: list[str],
    graph_type: str | None,
    out: dict[str, str],
    types_out: dict[str, str | None] | None = None,
    apps_out: AppInfo | None = None,
    strict: bool = False,
) -> None:
    """Run ``getByIds`` in BATCH_SIZE chunks, writing names into ``out``."""
    label = graph_type or "untyped"
    for i in range(0, len(ids), BATCH_SIZE):
        batch = ids[i : i + BATCH_SIZE]
        body: dict[str, object] = {"ids": batch}
        if graph_type:
            body["types"] = [graph_type]
        try:
            response = client.post(GRAPH_BATCH_URL, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            if strict and not _is_refusal(exc):
                raise GraphUnavailable(
                    f"Microsoft Graph did not answer for names ({type(exc).__name__})"
                ) from exc
            logger.warning("Graph getByIds(%s) failed: %s", label, exc)
            continue

        for obj in payload.get("value", []):
            pid = obj.get("id")
            if not pid:
                continue
            odata = obj.get("@odata.type", "")
            if graph_type == "user" or odata == "#microsoft.graph.user":
                name = obj.get("userPrincipalName") or obj.get("displayName")
            else:
                name = obj.get("displayName")
            if name:
                out[pid] = name
                if types_out is not None:
                    types_out[pid] = _GRAPH_TO_RBAC_TYPE.get(odata)
            if apps_out is not None and (
                graph_type == "servicePrincipal" or odata == "#microsoft.graph.servicePrincipal"
            ):
                apps_out[pid] = (obj.get("appOwnerOrganizationId"), obj.get("servicePrincipalType"))
