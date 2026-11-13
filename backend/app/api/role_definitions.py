"""Role definitions: the role guide's list, one role, and who holds it."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.role_risk import classify_permissions, risk_reasons
from app.core.scan_coverage import latest_done_scan_ids
from app.core.visibility import FORBIDDEN, Visibility, vis_of
from app.db.models import (
    RoleAssignment,
    RoleDefinition,
    Scan,
)
from app.db.session import get_session
from app.schemas.iam_access import (
    RoleDefinitionListItem,
    RoleDefinitionRead,
    RoleUsage,
    RoleUsageAssignment,
)
from app.tools.scope_ids import role_definition_guid

router = APIRouter(prefix="/iam-access", tags=["iam-access"])


# --------------------------------------------------------------------------------------
# Routes — role definitions
#
# Route ordering matters here: ``{role_id:path}`` is greedy, so it would
# swallow the trailing ``/usage`` if its route were registered first.
# Keep the more specific routes above the catch-all.
# --------------------------------------------------------------------------------------


@router.get("/role-definitions", response_model=list[RoleDefinitionListItem])
def list_role_definitions(
    session: Annotated[Session, Depends(get_session)],
    request: Request,
    q: str | None = Query(None, max_length=500),
    role_type: str | None = Query(None, description="Filter to BuiltInRole or CustomRole."),
    risk: str | None = Query(
        None,
        description="Filter to ``high`` to show only high-risk roles.",
    ),
    unused: bool = Query(
        False,
        description="When true, only return roles with zero assignments "
        "across all scans — cleanup candidates for stale custom roles.",
    ),
    sort: str = Query(
        "name",
        description="``name`` (default), ``usage`` (descending), or "
        "``risk`` (high first, then by name).",
    ),
) -> list[RoleDefinitionListItem]:
    """List every cached role definition with a usage count.

    Sorting is server-side so the SPA can stream the result into the
    master pane without re-sorting per filter change. Risk filtering
    is computed from ``permissions`` rather than persisted, so changes
    to the classifier take effect without a re-scan.
    """
    # "Assignment count" here is the number of currently-observed
    # grants of this role — distinct ``(principal_id, ra.scope)``
    # pairs sourced from the *latest* scan per root. Without this
    # filter the count is dominated by repeat scans of the same root
    # and by inherited rows persisted at multiple anchor scopes.
    latest_scan_ids = latest_done_scan_ids(session)
    rows = session.execute(select(RoleDefinition)).scalars().all()
    distinct_grants: dict[str, set[tuple[str, str]]] = {}
    vis = vis_of(request)
    if vis is not None:
        # Not an admin: counts over the grants they see; a custom role only when one of
        # those grants uses it (custom roles and their usage describe the whole tenant).
        distinct_grants = _visible_grants_by_role(vis)
        rows = [
            r
            for r in rows
            if r.role_type != "CustomRole" or r.role_definition_id in distinct_grants
        ]
    elif latest_scan_ids:
        triples = session.execute(
            select(
                RoleAssignment.role_definition_id,
                RoleAssignment.principal_id,
                RoleAssignment.scope,
            ).where(RoleAssignment.scan_id.in_(latest_scan_ids))
        ).all()
        for rdid, principal_id, scope in triples:
            distinct_grants.setdefault(role_definition_guid(rdid), set()).add((principal_id, scope))
    by_guid: dict[str, int] = {guid: len(grants) for guid, grants in distinct_grants.items()}
    items: list[RoleDefinitionListItem] = []
    needle = (q or "").strip().lower()
    for row in rows:
        risk_level = classify_permissions(row.permissions)
        if role_type and row.role_type != role_type:
            continue
        if risk and risk_level != risk:
            continue
        count = by_guid.get(row.role_definition_id, 0)
        if unused and count != 0:
            continue
        if needle and not (
            (row.role_name or "").lower().find(needle) >= 0
            or (row.description or "").lower().find(needle) >= 0
        ):
            continue
        item = RoleDefinitionListItem.model_validate(row)
        item.role_risk = risk_level
        item.assignment_count = count
        items.append(item)

    if sort == "usage":
        items.sort(key=lambda r: (-(r.assignment_count or 0), (r.role_name or "").lower()))
    elif sort == "risk":
        items.sort(
            key=lambda r: (
                0 if r.role_risk == "high" else 1,
                (r.role_name or "").lower(),
            )
        )
    else:
        items.sort(key=lambda r: (r.role_name or "").lower())
    return items


@router.get("/role-definitions/{role_id:path}/usage", response_model=RoleUsage)
def get_role_usage(
    role_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> RoleUsage:
    """Every current assignment of this role across every scan we have.

    "Current" means: for each ``scan.scope`` (scan root), take *only*
    that root's latest scan; from the RAs in those scans, dedupe by
    ``(principal_id, ra.scope)`` keeping the row from the freshest
    scan. That way:

    - A grant captured in scan #5 and removed in scan #6 disappears
      (scan #5 is superseded).
    - A grant observed via both an MG scan and a sub-scan of the same
      grant counts once, not twice.
    - A principal who has the role at two different scopes shows up
      twice.

    The list is ordered most-recent-scan first.
    """
    guid = role_definition_guid(role_id)
    role = session.get(RoleDefinition, guid)
    if role is None:
        raise HTTPException(status_code=404, detail=f"role definition {guid} not cached")

    latest_scan_ids = latest_done_scan_ids(session)
    if not latest_scan_ids:
        return RoleUsage(
            role_definition_id=guid,
            role_name=role.role_name,
            assignment_count=0,
            principal_count=0,
            scope_count=0,
            assignments=[],
        )

    rows = session.execute(
        select(RoleAssignment, Scan)
        .join(Scan, RoleAssignment.scan_id == Scan.id)
        .where(Scan.id.in_(latest_scan_ids))
        .order_by(Scan.created_at.desc(), RoleAssignment.scope)
    ).all()

    seen: set[tuple[str, str]] = set()
    assignments: list[RoleUsageAssignment] = []
    for ra, scan in rows:
        if role_definition_guid(ra.role_definition_id) != guid:
            continue
        key = (ra.principal_id, ra.scope)
        if key in seen:
            continue
        seen.add(key)
        assignments.append(
            RoleUsageAssignment(
                assignment_id=ra.id,
                scan_id=scan.id,
                scan_scope=scan.scope,
                scan_scope_level=scan.scope_level,
                scan_finished_at=scan.finished_at,
                scope=ra.scope,
                scope_name=ra.scope_name,
                scope_level=ra.scope_level,
                principal_id=ra.principal_id,
                principal_name=ra.principal_name,
                principal_type=ra.principal_type,
            )
        )

    return RoleUsage(
        role_definition_id=guid,
        role_name=role.role_name,
        assignment_count=len(assignments),
        principal_count=len({a.principal_id for a in assignments}),
        scope_count=len({a.scope for a in assignments}),
        assignments=assignments,
    )


@router.get("/role-definitions/{role_id:path}", response_model=RoleDefinitionRead)
def get_role_definition(
    role_id: str,
    session: Annotated[Session, Depends(get_session)],
    request: Request,
) -> RoleDefinitionRead:
    """Return the cached body of a role definition.

    Accepts either the bare role-definition GUID or a full ARM id
    (``/providers/Microsoft.Authorization/roleDefinitions/<guid>`` or
    ``/subscriptions/<sub>/.../roleDefinitions/<guid>``) — we always key
    on the GUID. Populated lazily during scans, so a freshly-deployed
    instance with no scans yet will 404 every role.
    """
    guid = role_definition_guid(role_id)
    row = session.get(RoleDefinition, guid)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(f"role definition {guid} not cached — run a scan that uses this role first"),
        )
    vis = vis_of(request)
    if (
        vis is not None
        and row.role_type == "CustomRole"
        and guid not in _visible_grants_by_role(vis)
    ):
        raise HTTPException(status_code=403, detail=FORBIDDEN)
    out = RoleDefinitionRead.model_validate(row)
    out.role_risk = classify_permissions(row.permissions)
    out.risk_reasons = risk_reasons(row.permissions)
    if vis is not None and row.role_type == "CustomRole":
        out.assignable_scopes = None  # the tenant's places where it may be given
    return out


def _visible_grants_by_role(vis: Visibility) -> dict[str, set[tuple[str, str]]]:
    """Role GUID → the distinct (principal, place) grants this person sees."""
    out: dict[str, set[tuple[str, str]]] = {}
    for ra in vis.view.ras:
        if vis.sees_grant(ra.scope, ra.principal_id):
            out.setdefault(role_definition_guid(ra.role_definition_id), set()).add(
                (ra.principal_id, ra.scope)
            )
    return out
