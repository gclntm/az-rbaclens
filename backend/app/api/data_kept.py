"""Settings › Data kept: what RBAC Lens keeps, how much is stored, and a preview.

Admins only (mounted with the admin dependency in ``app/main.py``). The
values live in ``core/retention_settings.py``: saved ones over the server's.
Saving says what changed in the audit log (``request.state.audit_detail``).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import retention_settings as rs
from app.core.config import settings
from app.core.retention import preview as retention_preview
from app.db.models import (
    AccessChange,
    ActivityCompactedDay,
    ActivityEvent,
    ActivityPull,
    AuditEvent,
    DirectoryAuditEvent,
    Scan,
)
from app.db.session import get_session

router = APIRouter(prefix="/settings/data-kept", tags=["data kept"])

# The tables behind each line, for its size on disk (Postgres only).
_TABLES: dict[str, tuple[str, ...]] = {
    "checks": (
        "scans",
        "role_assignments",
        "role_eligibilities",
        "group_memberships",
        "scan_group_resolutions",
        "findings",
        "scan_coverage",
    ),
    "activity_events": ("activity_events", "activity_compacted_days"),
    "entra_audit": ("directory_audit_events",),
    "changes": ("access_changes",),
    "audit": ("audit_events",),
    "pulls": ("activity_pulls",),
}


class Stored(BaseModel):
    count: int
    oldest: datetime | None
    # On disk, tables and indexes; null on SQLite.
    bytes: int | None = None


class DataKeptRead(BaseModel):
    # What applies now, where each value comes from (saved | server), and the server's.
    values: dict[str, int]
    sources: dict[str, str]
    server: dict[str, int]
    # (lowest, highest) per value; ``never`` lists those that also accept 0.
    bounds: dict[str, list[int]]
    never: list[str]
    stored: dict[str, Stored]
    # Routine app activity is folded into daily counts up to this day.
    folded_until: date | None
    # For the checks line's estimate: size of one check, and checks a day lately.
    bytes_per_check: int | None
    checks_per_day: float
    retention_interval_hours: int


class DataKeptWrite(BaseModel):
    # A value per setting; null clears it (back to the server's).
    values: dict[str, int | None]


class DataKeptPreview(BaseModel):
    # What a retention run with these values would delete (rows), and days it would fold.
    deletes: dict[str, int]


def _size(session: Session, tables: tuple[str, ...]) -> int | None:
    if session.get_bind().dialect.name != "postgresql":
        return None
    return sum(
        session.scalar(text("SELECT pg_total_relation_size(:t)"), {"t": t}) or 0 for t in tables
    )


def _stored(session: Session, column, where=None, tables: tuple[str, ...] = ()) -> Stored:
    q = select(func.count(), func.min(column))
    if where is not None:
        q = q.where(where)
    count, oldest = session.execute(q).one()
    return Stored(count=count or 0, oldest=oldest, bytes=_size(session, tables) if tables else None)


def _read(session: Session) -> DataKeptRead:
    stored = {
        "checks": _stored(session, Scan.created_at, Scan.status == "done", _TABLES["checks"]),
        "failed_checks": _stored(session, Scan.created_at, Scan.status == "failed"),
        "activity_events": _stored(
            session, ActivityEvent.occurred_at, tables=_TABLES["activity_events"]
        ),
        "entra_audit": _stored(
            session, DirectoryAuditEvent.occurred_at, tables=_TABLES["entra_audit"]
        ),
        "changes": _stored(session, AccessChange.occurred_at, tables=_TABLES["changes"]),
        "audit": _stored(session, AuditEvent.occurred_at, tables=_TABLES["audit"]),
        "pulls": _stored(session, ActivityPull.created_at, tables=_TABLES["pulls"]),
    }
    checks = stored["checks"]
    recent = session.scalar(
        select(func.count())
        .select_from(Scan)
        .where(Scan.status == "done", Scan.created_at >= datetime.now(UTC) - timedelta(days=14))
    )
    return DataKeptRead(
        values=asdict(rs.effective(session)),
        sources=rs.sources(session),
        server=asdict(rs.defaults()),
        bounds={name: list(b) for name, b in rs.BOUNDS.items()},
        never=sorted(rs.ZERO_MEANS_NEVER),
        stored=stored,
        folded_until=session.scalar(select(func.max(ActivityCompactedDay.day))),
        bytes_per_check=checks.bytes // checks.count if checks.bytes and checks.count else None,
        checks_per_day=round((recent or 0) / 14, 2),
        retention_interval_hours=settings.retention_interval_hours,
    )


@router.get("", response_model=DataKeptRead)
def read_data_kept(session: Annotated[Session, Depends(get_session)]) -> DataKeptRead:
    return _read(session)


@router.put("", response_model=DataKeptRead)
def save_data_kept(
    payload: DataKeptWrite, request: Request, session: Annotated[Session, Depends(get_session)]
) -> DataKeptRead:
    """Save the values (null clears one). Retention applies them at its next run,
    or at once with Apply now. The audit log records what changed."""
    try:
        before, after = rs.save(session, payload.values)
    except rs.InvalidRetention as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    changed = sorted(k for k in after if before[k] != after[k])
    detail: dict[str, Any] = {
        "before": {k: before[k] for k in changed},
        "after": {k: after[k] for k in changed},
    }
    request.state.audit_detail = detail
    return _read(session)


@router.post("/preview", response_model=DataKeptPreview)
def preview_data_kept(
    payload: DataKeptWrite, session: Annotated[Session, Depends(get_session)]
) -> DataKeptPreview:
    """What retention would delete with these values (saved ones for the rest),
    without saving or deleting anything."""
    try:
        values = rs.proposed(session, payload.values)
    except rs.InvalidRetention as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DataKeptPreview(deletes=retention_preview(session, values))
