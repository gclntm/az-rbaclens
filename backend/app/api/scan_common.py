"""What the check routes share: reading a check, its grants with their risk, and scope names."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.role_risk import classify_permissions
from app.db.models import (
    RoleAssignment,
    RoleDefinition,
    Scan,
)
from app.schemas.iam_access import (
    RoleAssignmentRead,
    ScanRead,
)
from app.tools.scan_walk import unread_scopes
from app.tools.scope_ids import role_definition_guid

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def scan_to_read(scan: Scan, count: int, unresolved_count: int = 0) -> ScanRead:
    # Friendly name of the root scope comes from the persisted hierarchy
    # ``{scope: {name, level, parent}}`` populated during discovery. For
    # subscriptions that turns a bare GUID into a display name in the UI.
    scope_name: str | None = None
    hierarchy = scan.hierarchy or {}
    meta = hierarchy.get(scan.scope)
    if isinstance(meta, dict):
        name = meta.get("name")
        if isinstance(name, str) and name:
            scope_name = name
    return ScanRead.model_validate(
        {
            **scan.__dict__,
            "scope_name": scope_name,
            "role_assignment_count": count,
            "unresolved_principals_count": unresolved_count,
            "unread_scopes": unread_scopes(hierarchy),
        }
    )


def get_scan_or_404(session: Session, scan_id: int) -> Scan:
    scan = session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"check {scan_id} not found")
    return scan


def count_role_assignments(session: Session, scan_id: int) -> int:
    return (
        session.scalar(
            select(func.count(RoleAssignment.id)).where(RoleAssignment.scan_id == scan_id)
        )
        or 0
    )


def role_risk_map(
    session: Session, role_definition_ids: list[str] | set[str]
) -> dict[str, str | None]:
    """Return ``{role_def_id: "high" | None}`` for each distinct id.

    Single query against ``role_definitions`` keyed on the bare GUID,
    then mapped back to the *full* ARM ids that RAs store. This keeps
    risk computation as O(distinct roles) per response — not per RA.
    Older scans where the role definition wasn't cached (pre-0012, or
    the SDK call failed) get ``None`` and fall back to the frontend's
    name-based heuristic.
    """
    if not role_definition_ids:
        return {}
    guid_to_arm: dict[str, list[str]] = {}
    for arm in role_definition_ids:
        guid_to_arm.setdefault(role_definition_guid(arm), []).append(arm)
    rows = session.execute(
        select(RoleDefinition.role_definition_id, RoleDefinition.permissions).where(
            RoleDefinition.role_definition_id.in_(guid_to_arm.keys())
        )
    ).all()
    out: dict[str, str | None] = dict.fromkeys(role_definition_ids)
    for guid, perms in rows:
        risk = classify_permissions(perms)
        for arm in guid_to_arm.get(guid, ()):
            out[arm] = risk
    return out


def serialize_ras_with_risk(
    session: Session, ras: list[RoleAssignment]
) -> list[RoleAssignmentRead]:
    """Bulk-serialize RAs with their ``role_risk`` field populated.

    Avoids an N+1 query against ``role_definitions`` by collecting the
    distinct role-def ids first.
    """
    risk = role_risk_map(session, {ra.role_definition_id for ra in ras})
    out: list[RoleAssignmentRead] = []
    for ra in ras:
        item = RoleAssignmentRead.model_validate(ra)
        item.role_risk = risk.get(ra.role_definition_id)
        out.append(item)
    return out


INFLIGHT_SCAN_DETAIL = (
    "A scan of this scope is already pending or running; wait for it to "
    "finish (or fail) before starting another."
)


def count_unresolved_principals(session: Session, scan_id: int) -> int:
    """Distinct principals whose Graph lookup didn't return a display name.

    A principal_id may show up in many RAs within one scan; count the
    *distinct* ids so the number reads as 'how many principals are
    unresolved' not 'how many rows are unresolved'.
    """
    return (
        session.scalar(
            select(func.count(func.distinct(RoleAssignment.principal_id)))
            .where(RoleAssignment.scan_id == scan_id)
            .where(RoleAssignment.principal_name.is_(None))
        )
        or 0
    )


def filename_slug(scope: str) -> str:
    """Compact filename component derived from an ARM scope path."""
    tail = scope.rstrip("/").rsplit("/", 1)[-1] or "scan"
    safe = "".join(c if c.isalnum() or c in "-._" else "-" for c in tail)
    return safe[:80] or "scan"


def short_level(scope: str) -> str:
    """Cheap fallback when neither hierarchy nor a row knows the level."""
    s = scope.lower()
    if "/managementgroups/" in s:
        return "mgmt_group"
    if "/resourcegroups/" in s and "/providers/" in s.split("/resourcegroups/", 1)[1]:
        return "resource"
    if "/resourcegroups/" in s:
        return "resource_group"
    if s.startswith("/subscriptions/"):
        return "subscription"
    return "resource"


# --------------------------------------------------------------------------------------
# Scope-string helpers
# --------------------------------------------------------------------------------------


def short_name(scope: str) -> str:
    """Return the trailing path component of an Azure scope string."""
    return scope.rstrip("/").rsplit("/", 1)[-1] or scope


def parent_scope(scope: str) -> str | None:
    """Return the parent scope, or None if `scope` is a root (mgmt group or sub).

    Examples:
      /providers/Microsoft.Management/managementGroups/foo            → None
      /subscriptions/abc                                              → None
      /subscriptions/abc/resourceGroups/rg                            → /subscriptions/abc
      /subscriptions/abc/resourceGroups/rg/providers/Microsoft.X/...  → /subscriptions/abc/resourceGroups/rg
    """
    parts = scope.rstrip("/").split("/")
    if len(parts) <= 3:
        return None
    if "/resourceGroups/" in scope and scope.count("/") >= 5:
        # An RG scope is /subscriptions/<id>/resourceGroups/<name> (4 segments after leading /).
        return "/".join(parts[:5]) if len(parts) > 5 else "/".join(parts[:3])
    if scope.startswith("/subscriptions/") and len(parts) >= 4:
        return "/".join(parts[:3])
    return None
