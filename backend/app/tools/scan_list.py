"""A check's second phase: list the grants on every place the walk found.

Reads role assignments place by place, the definitions of their roles, who
gave each grant, and resolves names through Microsoft Graph.
"""

from __future__ import annotations

import logging
from typing import Any

from azure.core.credentials import TokenCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.managementgroups import ManagementGroupsAPI
from azure.mgmt.subscription import SubscriptionClient

from app.core.azure import get_azure_transport
from app.core.config import settings
from app.db.models import (
    RoleAssignment,
)
from app.tools.grant_origin import name_creators, origin_fields
from app.tools.graph_groups import resolve_group_memberships
from app.tools.graph_resolver import resolve_principals
from app.tools.scan_walk import (
    Hierarchy,
    ProgressFn,
    ScopeUnreachable,
    discover_targets,
    mark_unread,
    stop_unless_refused,
)
from app.tools.scope_ids import (
    fallback_mg_name,
    level_from_scope,
    mg_parent_name,
    normalize_scope,
    role_definition_guid,
    scope_display_name,
    subscription_id_in_scope,
)

logger = logging.getLogger(__name__)


def collect_assignments(
    scope: str,
    scope_level: str,
    credential: TokenCredential,
    on_progress: ProgressFn,
) -> tuple[list[RoleAssignment], Hierarchy, dict[str, dict[str, Any]], dict[str, list[Any]]]:
    """Run all four scan phases.

    Returns ``(rows, hierarchy, role_definitions, group_memberships)``:

    - rows: deduped ``RoleAssignment`` records.
    - hierarchy: ``{scope: {name, level, parent, type?}}``.
    - role_definitions: ``{guid: {name, role_type, description,
      permissions, assignable_scopes}}`` for the role-definitions
      cache.
    - group_memberships: ``{group_id: [GroupMember]}`` for every
      group-typed principal in ``rows``. Used by the coverage
      analyzer to attribute member activity back to the group RA.
    """
    transport = get_azure_transport()
    sub_client = SubscriptionClient(credential, transport=transport)
    subscriptions = list(sub_client.subscriptions.list())
    if not subscriptions:
        raise RuntimeError("no subscriptions visible to this credential")

    sub_id_to_name: dict[str, str] = {
        s.subscription_id: s.display_name or s.subscription_id for s in subscriptions
    }
    mg_id_to_name: dict[str, str] = {}

    # AuthorizationManagementClient needs *some* subscription_id to bind to,
    # but ``list_for_scope`` and ``role_definitions.get_by_id`` accept any
    # ARM scope path. Use the scan's own subscription if it's a sub-rooted
    # scan, otherwise use any visible subscription.
    auth_sub_id = subscription_id_in_scope(scope) or subscriptions[0].subscription_id
    auth_client = AuthorizationManagementClient(credential, auth_sub_id, transport=transport)
    mg_client = ManagementGroupsAPI(credential, transport=transport)

    hierarchy: Hierarchy = {}

    # ---- Phase 1: discover ----------------------------------------------------
    targets = discover_targets(
        scope,
        scope_level,
        credential,
        mg_client,
        transport,
        sub_id_to_name,
        mg_id_to_name,
        hierarchy,
        on_progress,
    )

    # ---- Phase 2: list role assignments at each scope -------------------------
    rows: list[RoleAssignment] = []
    role_info_cache: dict[str, dict[str, Any]] = {}
    mg_lookup_failed: set[str] = set()
    # ``list_for_scope`` returns inherited assignments at every descendant,
    # so the same Azure assignment ID can come back many times during the
    # walk. Dedupe by ``ra.id`` to avoid duplicate rows.
    seen_assignment_ids: set[str] = set()
    total = len(targets)

    on_progress(0, total, f"Listing role assignments at {total} scopes")

    tenant_id = settings.arm_tenant_id or None

    def ensure_ancestor_chain(mg_id: str) -> None:
        """Walk up via ``parent.name`` until we hit a known scope or fail.

        Adds nodes to ``hierarchy`` and entries to ``mg_id_to_name`` along
        the way, so inherited assignments (e.g. tenant root → mg-contoso →
        mg-platform) get a fully-linked tree.

        The SP often lacks read access on the tenant root itself even when
        it can read descendants. In that case the SDK call 403s; we fall
        back to the well-known label "Tenant Root Group" when the failing
        MG id matches the credential's tenant id.

        All scopes are normalized to lowercase before being persisted so
        case mismatches with Azure's inherited-RA paths don't produce
        duplicate / orphan hierarchy entries.
        """
        current = mg_id
        while current:
            scope = normalize_scope(f"/providers/Microsoft.Management/managementGroups/{current}")
            if scope in hierarchy or current in mg_lookup_failed:
                return
            try:
                mg = mg_client.management_groups.get(group_id=current)
            except Exception as exc:  # noqa: BLE001 - one failing scope must not abort the scan
                logger.warning("could not resolve mgmt group %s: %s", current, exc)
                mg_lookup_failed.add(current)
                if tenant_id and current == tenant_id:
                    mg_id_to_name[current] = "Tenant Root Group"
                    hierarchy[scope] = {
                        "name": "Tenant Root Group",
                        "level": "mgmt_group",
                        "parent": None,
                    }
                return
            display_name = mg.display_name or fallback_mg_name(current, tenant_id)
            mg_id_to_name[current] = display_name
            parent_name = mg_parent_name(mg)
            parent_scope = (
                normalize_scope(f"/providers/Microsoft.Management/managementGroups/{parent_name}")
                if parent_name
                else None
            )
            hierarchy[scope] = {
                "name": display_name,
                "level": "mgmt_group",
                "parent": parent_scope,
            }
            current = parent_name

    root = normalize_scope(scope)
    for i, (target, _level) in enumerate(targets, start=1):
        try:
            assignments = list(auth_client.role_assignments.list_for_scope(target))
        except Exception as exc:
            if normalize_scope(target) == root:
                stop_unless_refused(exc, f"role assignments at {scope}")
                raise ScopeUnreachable(
                    f"role assignments at {scope} could not be listed — the scope does "
                    f"not exist or is not readable ({type(exc).__name__})"
                ) from exc
            status = stop_unless_refused(
                exc, f"role assignments at {target} (place {i} of {total})"
            )
            logger.warning("list_for_scope refused at %s (%s): %s", target, status, exc)
            if status == 403:
                mark_unread(hierarchy, target)
            assignments = []
        for ra in assignments:
            ra_id = getattr(ra, "id", None)
            if ra_id and ra_id in seen_assignment_ids:
                continue
            if ra_id:
                seen_assignment_ids.add(ra_id)
            actual_scope = normalize_scope(ra.scope or target)
            actual_level = level_from_scope(actual_scope)
            if actual_level == "mgmt_group":
                ensure_ancestor_chain(actual_scope.rsplit("/", 1)[-1])
            role_name, role_type = role_info(auth_client, ra.role_definition_id, role_info_cache)
            rows.append(
                RoleAssignment(
                    scope=actual_scope,
                    scope_name=scope_display_name(
                        actual_scope, actual_level, sub_id_to_name, mg_id_to_name
                    ),
                    scope_level=actual_level,
                    # Lowercased at ingest: object ids are case-insensitive
                    # GUIDs and every cross-scan lookup (registry, principal
                    # search, group member joins) keys on the lowercase form.
                    principal_id=(ra.principal_id or "").lower(),
                    principal_name=None,  # filled in phase 3
                    principal_type=ra.principal_type or "Unknown",
                    role_name=role_name,
                    role_definition_id=ra.role_definition_id,
                    role_type=role_type,
                    condition=getattr(ra, "condition", None) or None,
                    condition_version=getattr(ra, "condition_version", None) or None,
                    **origin_fields(ra),
                    raw=_to_dict(ra),
                )
            )
        on_progress(i, total, f"Listed {target}")

    # ---- Phase 3: resolve principal display names -----------------------------
    on_progress(total, total, "Resolving principal names…")
    principals_by_id: dict[str, str] = {
        r.principal_id: r.principal_type for r in rows if r.principal_id
    }
    apps: dict[str, tuple[str | None, str | None]] = {}
    # strict: a Graph outage must fail the check, not leave every identity
    # unnamed (which reads as "no longer exists: remove").
    name_map = resolve_principals(principals_by_id, credential, apps_out=apps, strict=True)
    for row in rows:
        if row.principal_id in name_map:
            row.principal_name = name_map[row.principal_id]
        if row.principal_id in apps:
            owner, kind = apps[row.principal_id]
            row.principal_app_owner = owner.lower() if owner else None
            row.principal_sp_type = kind
    # Who gave each grant: mostly identities named just above.
    name_creators(rows, principals_by_id, name_map, credential)

    # ---- Phase 4: resolve group transitive membership -------------------------
    # Only matters for coverage analysis (Phase 3 of the events feature):
    # Activity Log records the executing user/SP, not the group it
    # inherited the role via. Without this map, every group-typed RA
    # reads as 0% covered.
    on_progress(total, total, "Resolving group membership…")
    group_ids = sorted(
        {r.principal_id.lower() for r in rows if r.principal_id and r.principal_type == "Group"}
    )
    group_memberships = (
        resolve_group_memberships(group_ids, credential, strict=True) if group_ids else {}
    )

    return rows, hierarchy, role_info_cache, group_memberships


def role_info(
    auth_client: AuthorizationManagementClient,
    role_definition_id: str,
    cache: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return ``(role_name, role_type)`` for an Azure role definition id.

    ``cache`` maps the bare role-definition GUID to the full info dict
    (``name``, ``role_type``, ``description``, ``permissions``,
    ``assignable_scopes``). The caller persists ``cache`` to the
    ``role_definitions`` table after phase 2 — the UI's role detail
    card reads from there. We cache the *full* body even though the RA
    loop only needs name + type, so we don't have to re-fetch each role
    when we want to persist it.

    A role Azure refuses or no longer has (403 / 404: a deleted custom
    role still assigned) keeps its id and the scan goes on; it is not
    cached, so the next scan asks again. No answer at all (network,
    throttling, a server error) stops the scan like any other read:
    the grant would otherwise be saved without its role name.
    """
    guid = role_definition_guid(role_definition_id)
    if guid in cache:
        info = cache[guid]
        return info.get("name"), info.get("role_type")
    try:
        role_def = auth_client.role_definitions.get_by_id(role_definition_id)
    except Exception as exc:  # noqa: BLE001 - a refusal keeps the id; anything else stops the scan
        stop_unless_refused(exc, f"role definition {guid}")
        logger.debug("could not resolve role definition %s", role_definition_id)
        return None, None
    info: dict[str, Any] = {
        "name": role_def.role_name,
        "role_type": getattr(role_def, "role_type", None),
        "description": getattr(role_def, "description", None) or None,
        "permissions": _serialize_permissions(role_def),
        "assignable_scopes": list(getattr(role_def, "assignable_scopes", None) or []) or None,
    }
    cache[guid] = info
    return info["name"], info["role_type"]


def _serialize_permissions(role_def: Any) -> list[dict[str, list[str]]] | None:
    """Flatten an SDK ``RoleDefinition.permissions`` into JSON-friendly dicts."""
    perms = getattr(role_def, "permissions", None) or []
    out: list[dict[str, list[str]]] = []
    for p in perms:
        out.append(
            {
                "actions": list(getattr(p, "actions", None) or []),
                "notActions": list(getattr(p, "not_actions", None) or []),
                "dataActions": list(getattr(p, "data_actions", None) or []),
                "notDataActions": list(getattr(p, "not_data_actions", None) or []),
            }
        )
    return out or None


def _to_dict(obj: Any) -> dict[str, Any] | None:
    """Best-effort conversion of an SDK model to a JSON-serializable dict."""
    try:
        if hasattr(obj, "as_dict"):
            return obj.as_dict()
    except Exception:  # SDK serialisation is best effort
        logger.debug("as_dict failed on %s", type(obj).__name__, exc_info=True)
    return None
