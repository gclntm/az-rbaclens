"""IAM access API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.scan_common import (
    INFLIGHT_SCAN_DETAIL,
    count_role_assignments,
    count_unresolved_principals,
    get_scan_or_404,
    scan_to_read,
    serialize_ras_with_risk,
)
from app.core.jobs import create_scan as _create_scan
from app.db.models import (
    GroupMembership,
    RoleAssignment,
    Scan,
    ScanGroupResolution,
    Scope,
)
from app.db.session import get_session
from app.schemas.iam_access import (
    GroupMemberRead,
    GroupMembersResult,
    ScanCreate,
    ScanDiff,
    ScanRead,
)
from app.tools.iam_access import run_scan
from app.tools.scope_ids import parse_scope_input

router = APIRouter(prefix="/iam-access", tags=["iam-access"])


# --------------------------------------------------------------------------------------
# Routes — scans
# --------------------------------------------------------------------------------------


@router.post("/scans", response_model=ScanRead, status_code=201)
def start_scan(
    payload: ScanCreate,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> ScanRead:
    try:
        scope, scope_level = parse_scope_input(payload.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        scan = _create_scan(session, scope, scope_level)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=INFLIGHT_SCAN_DETAIL) from exc
    background.add_task(run_scan, scan.id)
    return scan_to_read(scan, 0)


@router.get("/scans", response_model=list[ScanRead])
def list_scans(
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=200),
) -> list[ScanRead]:
    rows = session.scalars(select(Scan).order_by(Scan.created_at.desc()).limit(limit)).all()
    return [
        scan_to_read(
            s,
            count_role_assignments(session, s.id),
            count_unresolved_principals(session, s.id),
        )
        for s in rows
    ]


@router.get("/scans/{scan_id}", response_model=ScanRead)
def get_scan(scan_id: int, session: Annotated[Session, Depends(get_session)]) -> ScanRead:
    scan = get_scan_or_404(session, scan_id)
    return scan_to_read(
        scan,
        count_role_assignments(session, scan_id),
        count_unresolved_principals(session, scan_id),
    )


@router.delete("/scans/{scan_id}", status_code=204)
def delete_scan(
    scan_id: int,
    session: Annotated[Session, Depends(get_session)],
    force: Annotated[bool, Query()] = False,
) -> None:
    """Delete a check and what it found. Refused (409) while it runs, and, unless
    ``force``, while it is still the current data of a place."""
    scan = get_scan_or_404(session, scan_id)
    if scan.status == "running":
        raise HTTPException(
            status_code=409, detail="This check is still running; wait for it to finish."
        )
    # The same guard as retention: a check some place still points to is its current data.
    owned = session.scalar(
        select(func.count()).select_from(Scope).where(Scope.latest_scan_id == scan_id)
    )
    if owned and not force:
        raise HTTPException(
            status_code=409,
            detail=f"This check is the current data for {owned} place(s). A newer check replaces it; "
            "then it can be deleted (retention does so by itself).",
        )
    session.delete(scan)
    session.commit()


@router.get(
    "/scans/{scan_id}/groups/{group_id}/members",
    response_model=GroupMembersResult,
)
def get_group_members(
    scan_id: int,
    group_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> GroupMembersResult:
    """List the transitive members of a group as observed during one scan.

    Reads ``group_memberships`` rows captured during the scan walk.
    404 when the (scan, group) pair has no rows — either an unknown
    group, or one whose membership Graph couldn't resolve (the
    Coverage tab renders that case as "membership unresolved").
    """
    get_scan_or_404(session, scan_id)
    gid = group_id.lower()
    rows = list(
        session.scalars(
            select(GroupMembership)
            .where(GroupMembership.scan_id == scan_id)
            .where(GroupMembership.group_id == gid)
            .order_by(GroupMembership.member_name, GroupMembership.member_id)
        ).all()
    )
    if not rows:
        # Distinguish empty-resolved (return []) from unresolved (404).
        resolved = session.scalar(
            select(ScanGroupResolution.id).where(
                ScanGroupResolution.scan_id == scan_id,
                ScanGroupResolution.group_id == gid,
            )
        )
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"group {gid} has no recorded membership in scan "
                    f"{scan_id} — re-run the scan, or grant the SP "
                    "GroupMember.Read.All on Microsoft Graph."
                ),
            )
    return GroupMembersResult(
        scan_id=scan_id,
        group_id=gid,
        members=[GroupMemberRead.model_validate(r) for r in rows],
    )


# --------------------------------------------------------------------------------------
# Routes — diff
# --------------------------------------------------------------------------------------


@router.get(
    "/scans/{old_id}/diff/{new_id}",
    response_model=ScanDiff,
)
def diff_scans(
    old_id: int,
    new_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> ScanDiff:
    """Compare two scans of the same scope and return what's added /
    removed / kept between them.

    Identity for the comparison is ``(scope, role_definition_id,
    principal_id)`` so a re-created Azure assignment (new id, same
    grant) is detected as kept rather than removed + added.
    """
    old_scan = get_scan_or_404(session, old_id)
    new_scan = get_scan_or_404(session, new_id)

    if old_scan.scope != new_scan.scope:
        raise HTTPException(
            status_code=400,
            detail=(
                "scans must have the same scope to be compared "
                f"(got {old_scan.scope!r} vs {new_scan.scope!r})"
            ),
        )

    old_rows = list(session.scalars(select(RoleAssignment).where(RoleAssignment.scan_id == old_id)))
    new_rows = list(session.scalars(select(RoleAssignment).where(RoleAssignment.scan_id == new_id)))

    def key(ra: RoleAssignment) -> tuple[str, str, str]:
        return (ra.scope, ra.role_definition_id, ra.principal_id)

    old_by_key = {key(ra): ra for ra in old_rows}
    new_by_key = {key(ra): ra for ra in new_rows}

    added_keys = new_by_key.keys() - old_by_key.keys()
    removed_keys = old_by_key.keys() - new_by_key.keys()
    kept_keys = old_by_key.keys() & new_by_key.keys()

    def to_rows(rows_by_key, keys):
        out = [rows_by_key[k] for k in keys]
        out.sort(key=lambda ra: (ra.scope, ra.role_name or "", ra.principal_id))
        return serialize_ras_with_risk(session, out)

    return ScanDiff(
        old_scan=scan_to_read(old_scan, count_role_assignments(session, old_id)),
        new_scan=scan_to_read(new_scan, count_role_assignments(session, new_id)),
        added=to_rows(new_by_key, added_keys),
        removed=to_rows(old_by_key, removed_keys),
        kept_count=len(kept_keys),
    )
