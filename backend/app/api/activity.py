"""Activity Log API: collections (``pulls``) and the events they stored."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.csv_safe import SafeDictWriter
from app.core.jobs import create_pull as _create_pull
from app.core.operation_names import names_for
from app.core.scheduler import retention_floor
from app.core.timeutil import as_utc
from app.db.models import ActivityEvent, ActivityPull
from app.db.session import get_session
from app.schemas.activity import (
    ActivityEventRead,
    ActivityPullCreate,
    ActivityPullRead,
)
from app.tools.activity import run_pull

router = APIRouter(prefix="/activity", tags=["activity"])


# --------------------------------------------------------------------------------------
# Pulls
# --------------------------------------------------------------------------------------


@router.post("/pulls", response_model=ActivityPullRead, status_code=201)
def start_pull(
    payload: ActivityPullCreate,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> ActivityPullRead:
    """Start an Activity Log pull. Runs in the background.

    Pulls overlap by design — we re-pull the same window after a
    while to catch late-arriving events. Dedupe is on
    ``(subscription_id, event_data_id)`` so it's safe.
    """
    start = _checked_start(payload.start, payload.end)
    try:
        pull = _create_pull(session, payload.subscription_id, start, payload.end)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=_INFLIGHT_PULL_DETAIL) from exc
    background.add_task(run_pull, pull.id)
    return ActivityPullRead.model_validate(pull)


def _checked_start(start: datetime, end: datetime) -> datetime:
    """Validate a requested window and return its start, capped at the
    retention floor.

    Azure rejects a start older than its 90-day retention on every page
    request, so a start at exactly 90 days back (the form's "90 days")
    fails once the pull has been paging a while. Such starts are moved
    up to the floor rather than refused; only a window that ends before
    the floor, with nothing left to pull, is a 400.
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if (end - start).days > 90:
        raise HTTPException(
            status_code=400,
            detail="a collection cannot go back more than 90 days (what Azure keeps)",
        )
    floor = retention_floor(datetime.now(UTC))
    if as_utc(end) <= floor:
        raise HTTPException(
            status_code=400,
            detail="this window ends before what Azure keeps (about 90 days back)",
        )
    return max(as_utc(start), floor)


_INFLIGHT_PULL_DETAIL = (
    "An activity pull for this subscription is already pending or running; "
    "wait for it to finish (or fail) before starting another."
)


@router.get("/pulls", response_model=list[ActivityPullRead])
def list_pulls(
    session: Annotated[Session, Depends(get_session)],
    subscription_id: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
) -> list[ActivityPullRead]:
    stmt = select(ActivityPull).order_by(ActivityPull.created_at.desc()).limit(limit)
    if subscription_id:
        stmt = stmt.where(ActivityPull.subscription_id == subscription_id)
    rows = session.scalars(stmt).all()
    return [ActivityPullRead.model_validate(r) for r in rows]


@router.get("/pulls/{pull_id}", response_model=ActivityPullRead)
def get_pull(pull_id: int, session: Annotated[Session, Depends(get_session)]) -> ActivityPullRead:
    pull = session.get(ActivityPull, pull_id)
    if pull is None:
        raise HTTPException(status_code=404, detail=f"collection {pull_id} not found")
    return ActivityPullRead.model_validate(pull)


@router.delete("/pulls/{pull_id}", status_code=204)
def delete_pull(pull_id: int, session: Annotated[Session, Depends(get_session)]) -> None:
    pull = session.get(ActivityPull, pull_id)
    if pull is None:
        raise HTTPException(status_code=404, detail=f"collection {pull_id} not found")
    session.delete(pull)
    session.commit()


# --------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------


@router.get("/events", response_model=list[ActivityEventRead])
def list_events(
    session: Annotated[Session, Depends(get_session)],
    subscription_id: str | None = Query(None, max_length=64),
    caller: str | None = Query(
        None,
        max_length=500,
        description="Case-insensitive substring match on the caller field.",
    ),
    operation_name: str | None = Query(
        None,
        max_length=500,
        description="Case-insensitive substring match on operationName.",
    ),
    status: str | None = Query(
        None,
        max_length=50,
        description="Exact match. Common values: Started, Accepted, Succeeded, Failed.",
    ),
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: int = Query(500, ge=1, le=5000),
) -> list[ActivityEventRead]:
    """Query persisted events with filters. Newest first.

    All filters are AND-combined. Caller / operation use substring
    match (case-insensitive) so an operator can search for
    ``alice`` or ``virtualMachines/write`` without typing the full
    string.
    """
    stmt = select(ActivityEvent).order_by(ActivityEvent.occurred_at.desc()).limit(limit)
    if subscription_id:
        stmt = stmt.where(ActivityEvent.subscription_id == subscription_id)
    if caller:
        # SQLAlchemy generates ILIKE on PG, LIKE on SQLite — both
        # case-insensitive when the column is a normal text column.
        from sqlalchemy import func as _f

        needle = f"%{caller.lower()}%"
        stmt = stmt.where(_f.lower(ActivityEvent.caller).like(needle))
    if operation_name:
        from sqlalchemy import func as _f

        needle = f"%{operation_name.lower()}%"
        stmt = stmt.where(_f.lower(ActivityEvent.operation_name).like(needle))
    if status:
        stmt = stmt.where(ActivityEvent.status == status)
    if from_time:
        stmt = stmt.where(ActivityEvent.occurred_at >= as_utc(from_time))
    if to_time:
        stmt = stmt.where(ActivityEvent.occurred_at <= as_utc(to_time))
    rows = session.scalars(stmt).all()
    return read_events(session, rows)


def read_events(session: Session, rows: Any) -> list[ActivityEventRead]:
    """Events as API rows, each with its operation in words."""
    names = names_for(session, (r.operation_name for r in rows))
    out = []
    for r in rows:
        item = ActivityEventRead.model_validate(r)
        item.operation_display = names.get(r.operation_name)
        out.append(item)
    return out


class _EventDetail(ActivityEventRead):
    raw: dict[str, Any] | None = None


def _build_event_query(
    *,
    subscription_id: str | None,
    caller: str | None,
    operation_name: str | None,
    status: str | None,
    from_time: datetime | None,
    to_time: datetime | None,
    limit: int,
):
    """Shared query builder used by both the list and export endpoints
    so filters stay in sync."""
    from sqlalchemy import func as _f

    stmt = select(ActivityEvent).order_by(ActivityEvent.occurred_at.desc()).limit(limit)
    if subscription_id:
        stmt = stmt.where(ActivityEvent.subscription_id == subscription_id)
    if caller:
        stmt = stmt.where(_f.lower(ActivityEvent.caller).like(f"%{caller.lower()}%"))
    if operation_name:
        stmt = stmt.where(
            _f.lower(ActivityEvent.operation_name).like(f"%{operation_name.lower()}%")
        )
    if status:
        stmt = stmt.where(ActivityEvent.status == status)
    if from_time:
        stmt = stmt.where(ActivityEvent.occurred_at >= as_utc(from_time))
    if to_time:
        stmt = stmt.where(ActivityEvent.occurred_at <= as_utc(to_time))
    return stmt


@router.get("/events/export")
def export_events(
    session: Annotated[Session, Depends(get_session)],
    fmt: str = Query("csv", pattern="^(csv|json)$"),
    subscription_id: str | None = Query(None, max_length=64),
    caller: str | None = Query(None, max_length=500),
    operation_name: str | None = Query(None, max_length=500),
    status: str | None = Query(None, max_length=50),
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: int = Query(50000, ge=1, le=200000),
) -> StreamingResponse:
    """Download the current filtered event set.

    Same filter shape as ``GET /events``. CSV uses
    ``Content-Disposition: attachment`` so the browser saves it; JSON
    is identical to the list payload, just unwrapped onto disk.

    A ``-filtered-`` segment appears in the filename when any filter is
    active so an operator can tell at a glance whether the download
    is a full slice or a narrow one (mirrors the iam-access export).
    """
    query = _build_event_query(
        subscription_id=subscription_id,
        caller=caller,
        operation_name=operation_name,
        status=status,
        from_time=from_time,
        to_time=to_time,
        limit=limit,
    ).with_only_columns(*_EXPORT_COLUMNS)

    filtered = any([subscription_id, caller, operation_name, status, from_time, to_time])
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    seg = "-filtered-" if filtered else "-"
    base = f"activity-events{seg}{ts}"

    if fmt == "csv":
        return StreamingResponse(
            _csv_chunks(query),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
        )
    header = {
        "exported_at": datetime.now(UTC).isoformat(),
        "filters": {
            "subscription_id": subscription_id,
            "caller": caller,
            "operation_name": operation_name,
            "status": status,
            "from": from_time.isoformat() if from_time else None,
            "to": to_time.isoformat() if to_time else None,
        },
    }
    return StreamingResponse(
        _json_chunks(query, header),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{base}.json"'},
    )


# Export columns, in CSV order. Never ``raw``: exports are for spreadsheets.
_EXPORT_COLUMNS = (
    ActivityEvent.occurred_at,
    ActivityEvent.subscription_id,
    ActivityEvent.caller,
    ActivityEvent.caller_type,
    ActivityEvent.operation_name,
    ActivityEvent.status,
    ActivityEvent.resource_id,
    ActivityEvent.resource_type,
    ActivityEvent.correlation_id,
    ActivityEvent.event_data_id,
    # A daily row of machine activity stands for event_count events, first_occurred_at to occurred_at.
    ActivityEvent.event_count,
    ActivityEvent.first_occurred_at,
)
_EXPORT_NAMES = [c.key for c in _EXPORT_COLUMNS]
_EXPORT_CHUNK = 5000


def _export_rows(query):
    """Yield chunks of plain row dicts from a server-side cursor.

    Opens its own session: a streamed body outlives the request's
    ``get_session`` dependency. Looked up at call time so tests that
    swap ``SessionLocal`` are honoured.
    """
    from app.db import session as db_session

    with db_session.SessionLocal() as session:
        result = session.execute(query.execution_options(yield_per=_EXPORT_CHUNK))
        for chunk in result.partitions():
            yield [
                {
                    name: (value.isoformat() if isinstance(value, datetime) else value)
                    for name, value in zip(_EXPORT_NAMES, row, strict=True)
                }
                for row in chunk
            ]


def _csv_chunks(query):
    buf = io.StringIO()
    writer = SafeDictWriter(buf, fieldnames=_EXPORT_NAMES)
    writer.writeheader()
    for rows in _export_rows(query):
        writer.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()
    if buf.tell():
        yield buf.getvalue()


def _json_chunks(query, header: dict[str, Any]):
    # Same document as before: the header object with an "events" array,
    # emitted incrementally instead of built in memory.
    yield json.dumps(header, indent=2)[:-2] + ',\n  "events": ['
    first = True
    for rows in _export_rows(query):
        for r in rows:
            yield ("\n    " if first else ",\n    ") + json.dumps(r)
            first = False
    yield "\n  ]\n}\n"


@router.get("/events/{event_id}", response_model=_EventDetail)
def get_event(event_id: int, session: Annotated[Session, Depends(get_session)]) -> _EventDetail:
    """Return one event including its raw SDK payload.

    Separate from the list endpoint so we don't ship the (sometimes
    large) ``raw`` JSON for every row in the events table — operators
    only need it when drilling into a specific event. Declared after
    ``/events/export`` so the literal path takes precedence over the
    ``int`` path-param matcher.
    """
    row = session.get(ActivityEvent, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"event {event_id} not found")
    out = _EventDetail.model_validate(row)
    out.operation_display = names_for(session, [row.operation_name]).get(row.operation_name)
    out.raw = row.raw
    return out
