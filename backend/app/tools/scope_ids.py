"""Azure scope ids: normalising them, telling their level, naming them, and reading what a person typed."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Azure RBAC inheritance flows strictly top-down. A child scope's level
# must be ≥ its parent's level (same-level only allowed for mgmt_group
# nesting, since one MG can be the parent of another MG).
LEVEL_ORDER: dict[str, int] = {
    "mgmt_group": 0,
    "subscription": 1,
    "resource_group": 2,
    "resource": 3,
}


def normalize_scope(scope: str) -> str:
    """Normalize an Azure scope path for case-insensitive storage and comparison.

    Azure ARM IDs are case-insensitive, but the SDK returns inconsistent
    casings — sometimes the canonical ``/subscriptions/.../managementGroups/x``,
    sometimes a fully-lowercased variant returned for inherited assignments.
    Mixing both forms in our DB / hierarchy makes ``in_tree`` membership fail
    silently and ``level_from_scope`` mis-classify, so we lower-case the
    whole path at every ingestion point.
    """
    return scope.lower() if scope else scope


def subscription_id_in_scope(scope: str) -> str | None:
    """Extract the subscription GUID from any ARM scope path containing one."""
    s = scope.lower()
    if "/subscriptions/" not in s:
        return None
    tail = s.split("/subscriptions/", 1)[1]
    return tail.split("/", 1)[0] or None


def parse_scope_input(user_input: str) -> tuple[str, str]:
    """Normalize a user-supplied scope into (full_arm_path, scope_level).

    Accepts:
      • full ARM paths: ``/providers/.../managementGroups/foo``,
        ``/subscriptions/<guid>``,
        ``/subscriptions/<guid>/resourceGroups/<rg>``,
        ``/subscriptions/<guid>/resourceGroups/<rg>/providers/<p>/<t>/<n>``
      • bare mgmt-group names: ``mg-platform``
      • bare subscription GUIDs: ``8a3f1234-…``
      • ``<sub-guid>/<rg-name>`` shorthand for a resource group
      • ``<sub-guid>/resourceGroups/<rg-name>`` (case-insensitive segment)
    """
    raw = user_input.strip()
    if not raw:
        raise ValueError("scope is empty")

    if raw.startswith("/"):
        level = level_from_scope(raw)
        return normalize_scope(raw), level

    # ``<sub-guid>/<rg-name>`` or ``<sub-guid>/resourceGroups/<rg-name>``.
    # A bare RG name can't be disambiguated from a bare MG name, so the
    # shorthand requires an explicit subscription GUID head. Any other
    # slash-containing input is rejected — it would otherwise silently
    # fall through to the bare-MG branch and produce a confusing 403.
    if "/" in raw:
        head, tail = raw.split("/", 1)
        if not looks_like_guid(head):
            raise ValueError(
                "scope shorthand: head must be a subscription GUID. Use "
                "'<sub-guid>/<rg-name>' or a full ARM path starting with '/'."
            )
        rg_segments = tail.split("/")
        if len(rg_segments) == 2 and rg_segments[0].lower() == "resourcegroups" and rg_segments[1]:
            rg_name = rg_segments[1]
        elif len(rg_segments) == 1 and rg_segments[0]:
            rg_name = rg_segments[0]
        else:
            raise ValueError(
                "scope shorthand: expected '<sub-guid>/<rg-name>' or "
                "'<sub-guid>/resourceGroups/<rg-name>'"
            )
        return (
            normalize_scope(f"/subscriptions/{head}/resourceGroups/{rg_name}"),
            "resource_group",
        )

    # Bare value — guess type.
    if looks_like_guid(raw):
        return normalize_scope(f"/subscriptions/{raw}"), "subscription"
    return (
        normalize_scope(f"/providers/Microsoft.Management/managementGroups/{raw}"),
        "mgmt_group",
    )


def looks_like_guid(value: str) -> bool:
    parts = value.split("-")
    return (
        len(parts) == 5
        and len(value) == 36
        and all(all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)
    )


def level_from_scope(scope: str) -> str:
    """Infer the scope level from an Azure scope path (case-insensitive).

    ``list_for_scope`` returns inherited assignments whose ``scope`` may
    sit at a higher level than the one we queried, so we need to derive
    the level from the actual path rather than trust the caller's hint.

    Azure mixes camelCase and all-lowercase casings for the same ARM
    segments (especially in inherited assignments), so we normalize the
    string before checking.
    """
    s = scope.lower()
    if "/managementgroups/" in s:
        return "mgmt_group"
    if "/resourcegroups/" in s:
        rg_tail = s.split("/resourcegroups/", 1)[1]
        return "resource" if "/" in rg_tail.rstrip("/") else "resource_group"
    if s.startswith("/subscriptions/"):
        return "subscription"
    return "resource"


def scope_display_name(
    scope: str,
    scope_level: str,
    sub_id_to_name: dict[str, str],
    mg_id_to_name: dict[str, str],
) -> str | None:
    """Build a human-readable scope label, or None if it can't be derived."""
    if scope_level == "mgmt_group":
        mg_id = scope.rsplit("/", 1)[-1]
        return mg_id_to_name.get(mg_id, mg_id)

    if scope_level == "subscription":
        sub_id = scope.rsplit("/", 1)[-1]
        return sub_id_to_name.get(sub_id, sub_id)

    if scope_level == "resource_group":
        parts = scope.split("/")
        try:
            sub_id = parts[parts.index("subscriptions") + 1]
            rg = parts[parts.index("resourceGroups") + 1]
        except (ValueError, IndexError):
            return None
        sub_label = sub_id_to_name.get(sub_id, sub_id)
        return f"{sub_label} / {rg}"

    return None


def fallback_mg_name(mg_id: str, tenant_id: str | None) -> str:
    """Best label when the SDK doesn't give us a display_name."""
    if tenant_id and mg_id == tenant_id:
        return "Tenant Root Group"
    return mg_id


def mg_parent_name(mg: Any) -> str | None:
    """Best-effort extraction of a mgmt-group's parent name.

    Different SDK versions expose this slightly differently; check the
    common attribute paths and fall back gracefully.
    """
    details = getattr(mg, "details", None)
    parent = getattr(details, "parent", None) if details else None
    if parent is None:
        return None
    # ``parent.name`` is the GUID-ish ID (matches our ``group_id`` parameter).
    name = getattr(parent, "name", None)
    if name:
        return name
    # Some SDK shapes put the ID on ``parent.id`` as a full ARM path.
    parent_id = getattr(parent, "id", None)
    if parent_id and "/managementGroups/" in parent_id:
        return parent_id.rsplit("/", 1)[-1]
    return None


def role_definition_guid(role_definition_id: str) -> str:
    """Return the bare GUID at the tail of an Azure role-definition id.

    Built-in roles use ``/providers/Microsoft.Authorization/roleDefinitions/<guid>``
    while custom roles can be scoped under a subscription
    (``/subscriptions/<sub>/providers/.../roleDefinitions/<guid>``). The
    GUID itself is unique across both, so it makes a sensible primary
    key for the cached role-definitions table.
    """
    return role_definition_id.rsplit("/", 1)[-1].lower()
