"""Scope tree / detail, principal search / detail, registry admin.

All counts come from :class:`app.core.current_view.CurrentView` — the
one definition of "current" shared with the scope page and the overview.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.current_view import CurrentView, current_view
from app.core.registry import rebuild_registries
from app.core.timeutil import as_utc
from app.core.visibility import vis_of
from app.db.models import Principal, Scan, Scope
from app.db.session import get_session
from app.schemas.registry import (
    LatestScanRef,
    PrincipalRead,
    RegistryRebuildResult,
    ScopeCrumb,
    ScopeDetail,
    ScopeNode,
    ScopeStats,
)

router = APIRouter(tags=["registry"])
admin_router = APIRouter(prefix="/settings/registry", tags=["registry"])

_LEVEL_RANK = {
    "directory": -1,
    "mgmt_group": 0,
    "subscription": 1,
    "resource_group": 2,
    "resource": 3,
}


def normalise_scope_param(raw: str) -> str:
    return "/" + raw.strip().strip("/").lower()


def latest_scan_refs(session: Session, scan_ids: set[int]) -> dict[int, LatestScanRef]:
    if not scan_ids:
        return {}
    now = datetime.now(UTC)
    out: dict[int, LatestScanRef] = {}
    for sid, finished in session.execute(
        select(Scan.id, Scan.finished_at).where(Scan.id.in_(scan_ids))
    ).all():
        age = None
        if finished is not None:
            age = (now - as_utc(finished)).days
        out[sid] = LatestScanRef(id=sid, finished_at=finished, age_days=age)
    return out


def _node(scope: Scope, refs: dict[int, LatestScanRef], view: CurrentView) -> ScopeNode:
    node = ScopeNode.model_validate(scope)
    node.latest_scan = refs.get(scope.latest_scan_id) if scope.latest_scan_id else None
    node.stats = ScopeStats(**view.scope_stats.get(scope.id, {}))
    node.children = []
    return node


def _sort_key(n: ScopeNode) -> tuple[int, str]:
    return (_LEVEL_RANK.get(n.level, 9), (n.name or n.id).lower())


# --------------------------------------------------------------------------------------
# Scopes
# --------------------------------------------------------------------------------------


@router.get("/scopes/tree", response_model=list[ScopeNode])
def scope_tree(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    root: str | None = Query(None, description="Return only the subtree under this scope id."),
    include_resources: bool = Query(False),
) -> list[ScopeNode]:
    """The scope hierarchy with per-node stats over the current RA set.

    Resources are omitted by default (a tenant can have tens of
    thousands); their RAs still count in ``assignments_below``.
    """
    view = current_view(session)
    stmt = select(Scope)
    if not include_resources:
        stmt = stmt.where(Scope.level != "resource")
    scopes = list(session.scalars(stmt).all())
    root_id = normalise_scope_param(root) if root else None
    if root_id is not None:
        keep = view.subtree(root_id)
        scopes = [s for s in scopes if s.id in keep]
    vis = vis_of(request)
    if vis is not None:
        # Their places and everything inside, with the places above for context.
        scopes = [s for s in scopes if vis.sees_place(s.id) or s.id in vis.above]
    refs = latest_scan_refs(session, {s.latest_scan_id for s in scopes if s.latest_scan_id})
    nodes = {s.id: _node(s, refs, view) for s in scopes}
    roots: list[ScopeNode] = []
    for s in scopes:
        node = nodes[s.id]
        if s.parent_id and s.parent_id in nodes and s.id != root_id:
            nodes[s.parent_id].children.append(node)
        else:
            roots.append(node)
    for node in nodes.values():
        node.children.sort(key=_sort_key)
    roots.sort(key=_sort_key)
    return roots


@router.get("/scopes/{scope_id:path}", response_model=ScopeDetail)
def scope_detail(
    scope_id: str,
    session: Annotated[Session, Depends(get_session)],
    include_resources: bool = Query(False),
) -> ScopeDetail:
    sid = normalise_scope_param(scope_id)
    scope = session.get(Scope, sid)
    if scope is None:
        raise HTTPException(status_code=404, detail=f"place {sid} not seen by any finished check")
    view = current_view(session)

    ancestors = [
        ScopeCrumb(id=a, name=view.name.get(a), level=view.level.get(a, "mgmt_group"))
        for a in reversed(view.ancestors(sid))
    ]
    child_stmt = select(Scope).where(Scope.parent_id == sid)
    if not include_resources:
        child_stmt = child_stmt.where(Scope.level != "resource")
    children_rows = list(session.scalars(child_stmt).all())
    refs = latest_scan_refs(
        session, {s.latest_scan_id for s in [scope, *children_rows] if s.latest_scan_id}
    )
    return ScopeDetail(
        scope=_node(scope, refs, view),
        ancestors=ancestors,
        children=sorted((_node(c, refs, view) for c in children_rows), key=_sort_key),
        first_seen_at=scope.first_seen_at,
        last_seen_at=scope.last_seen_at,
    )


# --------------------------------------------------------------------------------------
# Principals
# --------------------------------------------------------------------------------------


@router.get("/principals", response_model=list[PrincipalRead])
def list_principals(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: str | None = Query(None, description="Substring of id or display name, case-insensitive."),
    principal_type: str | None = None,
    level: Annotated[
        Literal["access", "keys", "change", "data", "view", "unknown"] | None,
        Query(description="Only identities whose strongest grant is at this capability level."),
    ] = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[PrincipalRead]:
    view = current_view(session)
    stmt = select(Principal)
    if level:
        wanted = [pid for pid, lvl in view.strongest_level.items() if lvl == level]
        stmt = stmt.where(Principal.id.in_(wanted))
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(Principal.id.like(needle), func.lower(Principal.display_name).like(needle))
        )
    if principal_type:
        stmt = stmt.where(Principal.principal_type == principal_type)
    vis = vis_of(request)
    stmt = stmt.order_by(func.coalesce(Principal.display_name, Principal.id))
    if vis is None:
        rows = list(session.scalars(stmt.limit(limit)).all())
    else:
        rows = [p for p in session.scalars(stmt).all() if vis.sees_principal(p.id)][:limit]
    ids = {p.id for p in rows}
    counts: dict[str, int] = {}
    for ra in view.ras:
        pid = ra.principal_id.lower()
        if pid in ids:
            counts[pid] = counts.get(pid, 0) + 1
    groups = view.groups_of(ids)
    from app.api.identity_facts import sees_sign_ins
    from app.core.signins import current_sign_ins

    sign_ins = current_sign_ins(
        session,
        {p.id for p in rows if p.principal_type == "User" and sees_sign_ins(vis, p.id)},
    )
    out: list[PrincipalRead] = []
    for p in rows:
        item = PrincipalRead.model_validate(p)
        item.assignment_count = counts.get(p.id, 0)
        item.group_count = len(groups.get(p.id, ()))
        item.level = view.strongest_level.get(p.id)
        if p.id in sign_ins:
            item.last_sign_in_at = sign_ins[p.id].last_at
        out.append(item)
    return out


# --------------------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------------------


@admin_router.post("/rebuild", response_model=RegistryRebuildResult)
def rebuild(session: Annotated[Session, Depends(get_session)]) -> RegistryRebuildResult:
    """Re-derive ``scopes`` and ``principals`` from every completed scan."""
    return RegistryRebuildResult(**rebuild_registries(session))
