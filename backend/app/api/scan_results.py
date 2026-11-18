"""One check's results: its grants, and the tree of places it walked."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.scan_common import (
    get_scan_or_404,
    parent_scope,
    role_risk_map,
    serialize_ras_with_risk,
    short_level,
    short_name,
)
from app.db.models import (
    RoleAssignment,
)
from app.db.session import get_session
from app.schemas.iam_access import (
    RoleAssignmentRead,
    TreeNode,
)
from app.tools.scope_ids import normalize_scope

router = APIRouter(prefix="/iam-access", tags=["iam-access"])


# --------------------------------------------------------------------------------------
# Routes — results
# --------------------------------------------------------------------------------------


@router.get("/scans/{scan_id}/role-assignments", response_model=list[RoleAssignmentRead])
def list_role_assignments(
    scan_id: int,
    session: Annotated[Session, Depends(get_session)],
    principal_type: str | None = None,
    role_name: str | None = None,
    scope_level: str | None = None,
) -> list[RoleAssignmentRead]:
    get_scan_or_404(session, scan_id)
    stmt = select(RoleAssignment).where(RoleAssignment.scan_id == scan_id)
    if principal_type:
        stmt = stmt.where(RoleAssignment.principal_type == principal_type)
    if role_name:
        stmt = stmt.where(RoleAssignment.role_name == role_name)
    if scope_level:
        stmt = stmt.where(RoleAssignment.scope_level == scope_level)
    stmt = stmt.order_by(RoleAssignment.scope, RoleAssignment.role_name)
    return serialize_ras_with_risk(session, list(session.scalars(stmt).all()))


@router.get("/scans/{scan_id}/tree", response_model=list[TreeNode])
def get_tree(scan_id: int, session: Annotated[Session, Depends(get_session)]) -> list[TreeNode]:
    """Build a tree rooted at the scan's ``scope``.

    Ancestor scopes captured during the walk (e.g. ``mg-contoso``, Tenant
    Root Group) are NOT rendered as their own nodes — instead, every
    role-assignment defined at an ancestor scope is re-attributed to the
    scan root, where the frontend renders it as "inherited from <scope>"
    using each row's stored ``scope_name``.

    Falls back to scope-string parent inference for legacy scans without
    ``scan.hierarchy``.
    """
    scan = get_scan_or_404(session, scan_id)
    rows = session.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.scan_id == scan_id)
        .order_by(RoleAssignment.scope, RoleAssignment.role_name)
    ).all()

    # Normalize all scope strings to lowercase for case-insensitive comparison.
    # Azure returns inconsistently-cased ARM IDs for inherited assignments,
    # and legacy scans may still have mixed-case entries persisted.
    raw_hierarchy: dict[str, dict[str, Any]] = scan.hierarchy or {}
    hierarchy: dict[str, dict[str, Any]] = {
        normalize_scope(k): {
            **v,
            "parent": normalize_scope(v["parent"]) if v.get("parent") else None,
        }
        for k, v in raw_hierarchy.items()
    }
    scan_root_scope = normalize_scope(scan.scope)

    # Compute the set of scopes at or below the scan root by walking parent
    # → child links in the persisted hierarchy. Anything else is treated as
    # an "above-root" ancestor (or unknown) and re-attributed to the root.
    if scan_root_scope in hierarchy:
        children_of: dict[str, list[str]] = defaultdict(list)
        for s, meta in hierarchy.items():
            parent = meta.get("parent")
            if parent:
                children_of[parent].append(s)
        in_tree: set[str] = set()
        stack = [scan_root_scope]
        while stack:
            s = stack.pop()
            in_tree.add(s)
            stack.extend(children_of.get(s, []))
    else:
        # Legacy / unusual scans — fall back to "everything is in-tree".
        in_tree = set(hierarchy.keys()) | {normalize_scope(ra.scope) for ra in rows}

    risk = role_risk_map(session, {ra.role_definition_id for ra in rows})

    fallback_levels: dict[str, str] = {}
    fallback_names: dict[str, str | None] = {}
    by_scope: dict[str, list[RoleAssignmentRead]] = defaultdict(list)
    # Synthetic nodes for resource scopes we encountered via RAs but did not
    # walk during phase 1. They get attached under the RG that contains them
    # (their nearest in-tree ancestor) so the tree shows actual resources
    # instead of dumping their RAs on the scan root.
    synthetic: dict[str, dict[str, Any]] = {}
    for ra in rows:
        ra_scope_norm = normalize_scope(ra.scope)
        if ra_scope_norm in in_tree:
            target_scope = ra_scope_norm
        else:
            parent = _path_ancestor_in_tree(ra_scope_norm, in_tree)
            if parent:
                target_scope = ra_scope_norm
                synthetic.setdefault(
                    ra_scope_norm,
                    {
                        "name": short_name(ra_scope_norm),
                        "level": ra.scope_level or short_level(ra_scope_norm),
                        "parent": parent,
                    },
                )
            else:
                # Genuinely above the scan root (mg-contoso, tenant root) →
                # re-attribute to scan root so it renders as inherited there.
                target_scope = scan_root_scope
        item = RoleAssignmentRead.model_validate(ra)
        item.role_risk = risk.get(ra.role_definition_id)
        by_scope[target_scope].append(item)
        fallback_levels[ra_scope_norm] = ra.scope_level
        fallback_names[ra_scope_norm] = ra.scope_name

    # Merge synthetic resource nodes into the hierarchy map for node materialization.
    for scope, meta in synthetic.items():
        hierarchy.setdefault(scope, meta)
        in_tree.add(scope)

    all_scopes = in_tree | set(by_scope.keys())

    nodes: dict[str, TreeNode] = {}
    for scope in all_scopes:
        meta = hierarchy.get(scope, {})
        level = meta.get("level") or fallback_levels.get(scope) or short_level(scope)
        name = meta.get("name") or fallback_names.get(scope) or short_name(scope)
        nodes[scope] = TreeNode(
            scope=scope,
            scope_level=level,  # type: ignore[arg-type]
            name=name,
            resource_type=meta.get("type"),
            role_assignments=by_scope.get(scope, []),
        )

    # Link parents only within the in-tree set so ancestors don't surface.
    roots: list[TreeNode] = []
    for scope, node in nodes.items():
        meta = hierarchy.get(scope)
        parent = meta.get("parent") if meta else parent_scope(scope)
        if parent and parent in nodes:
            nodes[parent].children.append(node)
        else:
            roots.append(node)

    _sort_tree(roots)
    return roots


_LEVEL_ORDER = {
    "directory": -1,
    "mgmt_group": 0,
    "subscription": 1,
    "resource_group": 2,
    "resource": 3,
}


def _sort_tree(nodes: list[TreeNode]) -> None:
    """Sort siblings by level then name, recursively, so the tree renders deterministically."""
    nodes.sort(key=lambda n: (_LEVEL_ORDER.get(n.scope_level, 99), n.name.lower()))
    for n in nodes:
        _sort_tree(n.children)


def _path_ancestor_in_tree(scope: str, in_tree: set[str]) -> str | None:
    """Walk up an ARM scope path to find an ancestor that's in ``in_tree``.

    Used to attach RAs at scopes we never walked (typically resources or
    sub-resources under an RG) to their nearest containing scope, instead
    of incorrectly re-attributing them to the scan root.

    For ``/subscriptions/<sub>/resourceGroups/<rg>/providers/<rp>/<type>/<name>...``
    this peels off path segments from the right until either an in_tree
    scope is hit or we run out of path.
    """
    s = scope
    # Walk up by stripping trailing /<segment> chunks.
    while "/" in s:
        s = s.rsplit("/", 1)[0]
        if not s:
            return None
        if s in in_tree:
            return s
    return None
