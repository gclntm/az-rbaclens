"""Audit log read endpoint.

Mounted as admin-only — see ``main.py``. Pagination via ``limit`` /
``offset`` query params, newest first. Returns a flat list; the UI
renders it as a simple table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.db.session import get_session

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    actor: str | None
    actor_name: str | None
    method: str
    path: str
    status_code: int
    client_ip: str | None = None
    # What the change changed, when the endpoint said so (e.g. Settings › Data kept).
    detail: dict[str, Any] | None = None


@router.get("/events", response_model=list[AuditEventRead])
def list_events(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEventRead]:
    stmt = (
        select(AuditEvent)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = session.scalars(stmt).all()
    return [AuditEventRead.model_validate(r) for r in rows]
