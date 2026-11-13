"""A check's first phase: walk down from its root and list the places to read.

Management groups, subscriptions and resource groups, with their names, parents
and tags. A place Azure refuses is marked unread; a scope that cannot be reached
at all stops the check (``ScopeUnreachable``), and losing the connection midway
stops it too (``ScanInterrupted``), so a half-read check is never recorded as done.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from azure.core.credentials import TokenCredential
from azure.mgmt.managementgroups import ManagementGroupsAPI
from azure.mgmt.resource import ResourceManagementClient

from app.core.config import settings
from app.tools.scope_ids import (
    LEVEL_ORDER,
    fallback_mg_name,
    normalize_scope,
    subscription_id_in_scope,
)

logger = logging.getLogger(__name__)


ProgressFn = Callable[[int, int | None, str], None]
"""Callback signature: (done, total_or_None, message)."""


Hierarchy = dict[str, dict[str, Any]]
"""``{scope: {"name": str, "level": str, "parent": str | None}}``."""


class ScopeUnreachable(RuntimeError):
    """The scan's *root* scope doesn't exist or the credential can't read it.

    Refusals below the root (a 403 on one subscription inside a management
    group, one resource group that can't be listed) are skipped so the rest
    of the walk still lands. A failure at the root used to be skipped the
    same way, turning a typo'd scope into a ``done`` snapshot with 0
    assignments — and a phantom scope in the registry.
    """


class ScanInterrupted(RuntimeError):
    """Azure stopped answering partway (network, proxy, throttling, 5xx).

    The scan fails as a whole and nothing is saved: going on would record
    the places it could not read as having no grants, which the changes
    feed would report as removals and the to-do list as work done. The
    scheduler tries again shortly (``core/scheduler.RETRY_AFTER``).
    """


def _refusal_status(exc: Exception) -> int | None:
    """403 or 404 when Azure *answered* that a place can't be read or isn't
    there: a stable fact, safe to skip. ``None`` for everything else (no
    answer at all, throttling, a server error, a failed sign-in)."""
    status = getattr(exc, "status_code", None)
    return status if status in (403, 404) else None


def stop_unless_refused(exc: Exception, what: str) -> int:
    """The refusal status, or raise :class:`ScanInterrupted` for ``what``."""
    status = _refusal_status(exc)
    if status is None:
        detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
        raise ScanInterrupted(
            f"could not read {what}: Azure did not answer ({type(exc).__name__}"
            f"{': ' + detail if detail else ''}). Nothing was saved."
        ) from exc
    return status


def discover_targets(
    root_scope: str,
    root_level: str,
    credential: TokenCredential,
    mg_client: ManagementGroupsAPI,
    transport: Any,
    sub_id_to_name: dict[str, str],
    mg_id_to_name: dict[str, str],
    hierarchy: Hierarchy,
    on_progress: ProgressFn,
) -> list[tuple[str, str]]:
    """Walk the hierarchy below ``root_scope`` and return scope targets.

    Dispatches based on ``root_level``:

    - ``mgmt_group``     → child MGs + subscriptions → resource groups
    - ``subscription``   → resource groups in this sub
    - ``resource_group`` → just the RG itself (resources not enumerated)
    - ``resource``       → just the resource itself
    """
    targets: list[tuple[str, str]] = []

    def emit_progress(extra: str = "") -> None:
        suffix = f" — {extra}" if extra else ""
        on_progress(0, None, f"Discovering scopes ({len(targets)} found){suffix}")

    emit_progress()

    def add_hierarchy_node(
        scope: str, level: str, name: str | None, parent_scope: str | None
    ) -> str:
        """Register a node in the hierarchy. Returns the normalized scope."""
        scope = normalize_scope(scope)
        parent_scope = normalize_scope(parent_scope) if parent_scope else None
        # Azure RBAC inheritance is strictly top-down. Catch any mis-walked
        # case where a child would claim a deeper-level scope as its parent.
        if parent_scope and parent_scope in hierarchy:
            parent_level = hierarchy[parent_scope].get("level")
            if parent_level and LEVEL_ORDER.get(parent_level, 0) > LEVEL_ORDER.get(level, 0):
                logger.error(
                    "invalid hierarchy direction: parent %s (%s) is below child %s (%s)",
                    parent_scope,
                    parent_level,
                    scope,
                    level,
                )
        hierarchy[scope] = {"name": name, "level": level, "parent": parent_scope}
        return scope

    def add_node(scope: str, level: str, name: str | None, parent_scope: str | None) -> None:
        """Hierarchy node + ``list_for_scope`` target."""
        scope = add_hierarchy_node(scope, level, name, parent_scope)
        targets.append((scope, level))

    def enumerate_resources(rg_scope: str, sub_id: str, rg_name: str) -> None:
        """Add every resource in ``rg_name`` as a hierarchy node under ``rg_scope``.

        Resource RAs are already captured by ``list_for_scope`` at the RG, so
        we only need to register the resources in the hierarchy — no extra
        targets. Without this, resources without any direct RA never appear
        in the tree.
        """
        try:
            res_client = ResourceManagementClient(credential, sub_id, transport=transport)
            for r in res_client.resources.list_by_resource_group(rg_name):
                if not r.id or not r.name:
                    continue
                normalized = add_hierarchy_node(r.id, "resource", r.name, rg_scope)
                # Azure types look like ``Microsoft.Storage/storageAccounts``.
                # We store the full string so the UI can filter / display
                # whatever granularity it wants; lower-cased for the same
                # case-insensitive-match reasons as the scope path.
                if r.type:
                    hierarchy[normalized]["type"] = r.type.lower()
        except Exception as exc:  # noqa: BLE001 - a refusal is skipped; anything else stops the scan
            stop_unless_refused(exc, f"the resources of {rg_scope}")
            logger.warning("could not list resources for %s: %s", rg_scope, exc)

    tenant_id = settings.arm_tenant_id or None

    def walk_mg(mg_name: str, parent_scope: str | None, *, is_root: bool = False) -> None:
        try:
            tree = mg_client.management_groups.get(group_id=mg_name, expand="children")
        except Exception as exc:
            if is_root:
                stop_unless_refused(exc, f"management group {mg_name}")
                raise ScopeUnreachable(
                    f"management group {mg_name!r} was not found or is not readable "
                    f"by the service principal ({type(exc).__name__})"
                ) from exc
            if stop_unless_refused(exc, f"management group {mg_name}") == 403:
                child = add_hierarchy_node(
                    f"/providers/Microsoft.Management/managementGroups/{mg_name}",
                    "mgmt_group",
                    mg_id_to_name.get(mg_name),
                    parent_scope,
                )
                mark_unread(hierarchy, child)
            logger.warning("could not access mgmt group %s: %s", mg_name, exc)
            return
        display_name = tree.display_name or fallback_mg_name(mg_name, tenant_id)
        mg_id_to_name[mg_name] = display_name

        scope = f"/providers/Microsoft.Management/managementGroups/{mg_name}"
        add_node(scope, "mgmt_group", display_name, parent_scope)
        emit_progress(f"mgmt group {mg_name}")

        for child in tree.children or []:
            child_type = (child.type or "").lower()
            if "managementgroups" in child_type:
                if child.display_name:
                    mg_id_to_name[child.name] = child.display_name
                walk_mg(child.name, parent_scope=scope)
            elif "subscriptions" in child_type:
                if child.display_name:
                    sub_id_to_name[child.name] = child.display_name
                walk_subscription(child.name, parent_scope=scope)

    def walk_subscription(sub_id: str, parent_scope: str | None) -> None:
        sub_scope = f"/subscriptions/{sub_id}"
        sub_label = sub_id_to_name.get(sub_id, sub_id)
        add_node(sub_scope, "subscription", sub_label, parent_scope)
        emit_progress(f"subscription {sub_label}")

        try:
            res_client = ResourceManagementClient(credential, sub_id, transport=transport)
            _keep_tags(hierarchy, sub_scope, _subscription_tags(res_client, sub_scope))
            for rg in res_client.resource_groups.list():
                rg_scope = f"{sub_scope}/resourceGroups/{rg.name}"
                add_node(
                    rg_scope,
                    "resource_group",
                    f"{sub_label} / {rg.name}",
                    sub_scope,
                )
                _keep_tags(hierarchy, rg_scope, rg.tags)
                enumerate_resources(rg_scope, sub_id, rg.name)
        except Exception as exc:  # noqa: BLE001 - a refusal is skipped; anything else stops the scan
            if stop_unless_refused(exc, f"the resource groups of subscription {sub_label}") == 403:
                mark_unread(hierarchy, sub_scope)
            logger.warning("could not list RGs for sub %s: %s", sub_id, exc)
        emit_progress(f"subscription {sub_label} done")

    if root_level == "mgmt_group":
        mg_name = root_scope.rsplit("/", 1)[-1]
        walk_mg(mg_name, parent_scope=None, is_root=True)
    elif root_level == "subscription":
        sub_id = root_scope.rsplit("/", 1)[-1]
        # The visible-subscription list doubles as the existence check.
        visible = {k.lower(): k for k in sub_id_to_name}
        if sub_id.lower() not in visible:
            raise ScopeUnreachable(
                f"subscription {sub_id} is not visible to the service principal "
                "(wrong id, or no role on it)"
            )
        walk_subscription(visible[sub_id.lower()], parent_scope=None)
    elif root_level == "resource_group":
        # ``list_for_scope`` at the RG returns RAs at, above, and below the RG
        # — so no extra target calls are needed for child resources. We only
        # enumerate the resources here to populate the hierarchy so resources
        # without any direct RA still show up in the tree.
        rg_name = root_scope.rsplit("/", 1)[-1]
        sub_id = subscription_id_in_scope(root_scope) or ""
        sub_label = sub_id_to_name.get(sub_id, sub_id)
        add_node(
            root_scope,
            "resource_group",
            f"{sub_label} / {rg_name}" if sub_label else rg_name,
            None,
        )
        if sub_id:
            enumerate_resources(normalize_scope(root_scope), sub_id, rg_name)
        emit_progress(f"resource group {rg_name}")
    elif root_level == "resource":
        # Single resource — no children.
        add_node(root_scope, "resource", root_scope.rsplit("/", 1)[-1], None)
        emit_progress("resource")
    else:
        raise ValueError(f"unsupported scope_level: {root_level}")

    return targets


def _subscription_tags(res_client: Any, sub_scope: str) -> dict[str, str] | None:
    """Tags on a subscription (``Microsoft.Resources/tags/read``); ``None`` when unreadable."""
    try:
        result = res_client.tags.get_at_scope(sub_scope)
        return dict(result.properties.tags or {}) if result and result.properties else None
    except Exception as exc:  # noqa: BLE001 - tags are optional, never fail the walk
        logger.debug("could not read tags on %s: %s", sub_scope, exc)
        return None


def _keep_tags(hierarchy: dict[str, Any], scope: str, tags: dict[str, str] | None) -> None:
    """Store a place's tags on its hierarchy node (responsible-person suggestions)."""
    node = hierarchy.get(normalize_scope(scope))
    if node is not None and tags:
        node["tags"] = {str(k): str(v) for k, v in tags.items()}


def mark_unread(hierarchy: Hierarchy, scope: str) -> None:
    """Flag a place the identity is not allowed to read (403): its own grants
    are unknown to this scan, not absent."""
    node = hierarchy.get(normalize_scope(scope))
    if node is not None:
        node["unread"] = True


def unread_scopes(hierarchy: Hierarchy | None) -> list[str]:
    """Places a scan was refused on, from its stored hierarchy."""
    return sorted(
        s for s, n in (hierarchy or {}).items() if isinstance(n, dict) and n.get("unread")
    )
