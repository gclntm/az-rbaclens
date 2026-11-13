"""One check's grants as a file (CSV or JSON)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.scan_common import filename_slug, get_scan_or_404
from app.core.csv_safe import safe_writer
from app.db.models import (
    RoleAssignment,
)
from app.db.session import get_session

router = APIRouter(prefix="/iam-access", tags=["iam-access"])


# --------------------------------------------------------------------------------------
# Routes — export
# --------------------------------------------------------------------------------------


@router.get("/scans/{scan_id}/export")
def export_scan(
    scan_id: int,
    session: Annotated[Session, Depends(get_session)],
    fmt: str = Query("json", pattern="^(json|csv)$"),
    principal_type: str | None = None,
    role_name: str | None = None,
    scope_level: str | None = None,
) -> StreamingResponse:
    """Export role assignments as JSON or CSV.

    Accepts the same filter params as ``GET /scans/{id}/role-assignments``
    so the Inspector can export *its current view* — not just the whole
    scan. A "-filtered" suffix is added to the filename when any filter
    is applied.
    """
    scan = get_scan_or_404(session, scan_id)
    stmt = select(RoleAssignment).where(RoleAssignment.scan_id == scan_id)
    if principal_type:
        stmt = stmt.where(RoleAssignment.principal_type == principal_type)
    if role_name:
        stmt = stmt.where(RoleAssignment.role_name == role_name)
    if scope_level:
        stmt = stmt.where(RoleAssignment.scope_level == scope_level)
    stmt = stmt.order_by(RoleAssignment.scope, RoleAssignment.role_name)
    rows = session.scalars(stmt).all()

    is_filtered = bool(principal_type or role_name or scope_level)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = f"iam-access-{filename_slug(scan.scope)}-{'filtered-' if is_filtered else ''}{timestamp}"

    if fmt == "csv":
        buf = io.StringIO()
        writer = safe_writer(buf)
        writer.writerow(
            [
                "scope",
                "scope_name",
                "scope_level",
                "role_name",
                "principal_type",
                "principal_name",
                "principal_id",
                "role_definition_id",
            ]
        )
        for ra in rows:
            writer.writerow(
                [
                    ra.scope,
                    ra.scope_name or "",
                    ra.scope_level,
                    ra.role_name or "",
                    ra.principal_type,
                    ra.principal_name or "",
                    ra.principal_id,
                    ra.role_definition_id,
                ]
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
        )

    payload = {
        "scan": {
            "id": scan.id,
            "scope": scan.scope,
            "scope_level": scan.scope_level,
            "status": scan.status,
            "started_at": scan.started_at.isoformat() if scan.started_at else None,
            "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
        },
        "role_assignments": [
            {
                "scope": ra.scope,
                "scope_name": ra.scope_name,
                "scope_level": ra.scope_level,
                "role_name": ra.role_name,
                "principal_type": ra.principal_type,
                "principal_name": ra.principal_name,
                "principal_id": ra.principal_id,
                "role_definition_id": ra.role_definition_id,
            }
            for ra in rows
        ],
    }
    body = json.dumps(payload, indent=2)
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{base}.json"'},
    )
