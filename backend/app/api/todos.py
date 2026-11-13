"""``/todos``: the changes to make in Azure after finished reviews.

Anyone signed in reads the to-dos for their places and their reviews; the
round's reviewer or an admin dismisses one. The daily check closes them.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import todos as core
from app.core.advice_texts import comment_text
from app.core.csv_safe import safe_writer
from app.core.current_view import CurrentView, current_view
from app.core.security import require_auth, who_is
from app.core.visibility import Visibility, vis_of
from app.db.models import ReviewRound, Todo
from app.db.session import get_session
from app.schemas.todos import TodoDismiss, TodoRead, TodoStatus, TodoSummary

router = APIRouter(tags=["todos"])
Claims = Annotated[dict[str, Any] | None, Depends(require_auth)]
Show = Literal["mine", "all"]


def _sees(vis: Visibility | None, t: Todo) -> bool:
    return vis is None or t.reviewer_id == vis.oid or vis.sees_grant_at(t.scope)


def _mine(t: Todo, oid: str | None) -> bool:
    """With sign-in off there is no "me": everything counts as mine."""
    return oid is None or t.reviewer_id == oid


def _query(
    session: Session,
    request: Request,
    claims: dict[str, Any] | None,
    status: TodoStatus | Literal["all"],
    show: Show,
    round_id: int | None,
) -> tuple[list[Todo], str | None, bool]:
    oid, _, admin = who_is(session, claims)
    stmt = select(Todo).order_by(Todo.status, Todo.due_at, Todo.id)
    if status != "all":
        stmt = stmt.where(Todo.status == status)
    if round_id is not None:
        stmt = stmt.where(Todo.round_id == round_id)
    vis = vis_of(request)
    rows = [
        t for t in session.scalars(stmt).all() if _sees(vis, t) and (show == "all" or _mine(t, oid))
    ]
    return rows, oid, admin


def _read(
    t: Todo, periods: dict[int, str], view: CurrentView | None, oid: str | None, admin: bool
) -> TodoRead:
    replacements: list[str] = []
    if view is not None and t.action == "change":
        replacements = sorted(
            {
                ra.role_name or "a role"
                for ra in view.ras
                if ra.principal_id.lower() == t.principal_id
                and ra.scope.lower() == t.scope
                and ra.role_name != t.role_name
            }
        )
    return TodoRead(
        id=t.id,
        round_id=t.round_id,
        period=periods.get(t.round_id),
        item_id=t.item_id,
        grant_id=t.grant_fingerprint,
        action=t.action,
        scope=t.scope,
        scope_name=t.scope_name,
        principal_id=t.principal_id,
        principal_name=t.principal_name,
        principal_type=t.principal_type,
        role_name=t.role_name,
        note=t.note,
        advice_rule=t.advice_rule,
        how=list(t.how or []),
        commands=list(t.commands or []),
        reviewer_name=t.reviewer_name,
        decided_by_name=t.decided_by_name,
        created_at=t.created_at,
        due_at=t.due_at,
        status=t.status,
        overdue=core.is_overdue(t),
        done_at=t.done_at,
        dismissed_reason=t.dismissed_reason,
        dismissed_by_name=t.dismissed_by_name,
        replacements=replacements,
        can_dismiss=t.status == "open" and (admin or (oid is not None and oid == t.reviewer_id)),
    )


def _periods(session: Session, rows: list[Todo]) -> dict[int, str]:
    ids = {t.round_id for t in rows}
    return (
        dict(
            session.execute(
                select(ReviewRound.id, ReviewRound.period).where(ReviewRound.id.in_(ids))
            ).all()
        )
        if ids
        else {}
    )


@router.get("/todos", response_model=list[TodoRead])
def list_todos(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
    status: TodoStatus | Literal["all"] = "open",
    show: Show = "mine",
    round_id: int | None = Query(None, ge=1),
) -> list[TodoRead]:
    """Open first, oldest due first. ``show=mine``: the rounds you review (everything with sign-in off)."""
    rows, oid, admin = _query(session, request, claims, status, show, round_id)
    view = current_view(session) if any(t.action == "change" for t in rows) else None
    periods = _periods(session, rows)
    return [_read(t, periods, view, oid, admin) for t in rows]


@router.get("/todos/summary", response_model=TodoSummary)
def todo_summary(
    request: Request, session: Annotated[Session, Depends(get_session)], claims: Claims
) -> TodoSummary:
    rows, oid, _ = _query(session, request, claims, "open", "all", None)
    now = datetime.now(UTC)
    mine = [t for t in rows if _mine(t, oid)]
    return TodoSummary(
        open=len(rows),
        overdue=sum(core.is_overdue(t, now) for t in rows),
        mine_open=len(mine),
        mine_overdue=sum(core.is_overdue(t, now) for t in mine),
    )


@router.post("/todos/{todo_id}/dismiss", response_model=TodoRead)
def dismiss_todo(
    todo_id: int,
    payload: TodoDismiss,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
) -> TodoRead:
    oid, name, admin = who_is(session, claims)
    todo = session.get(Todo, todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="to-do not found")
    if not (admin or (oid is not None and oid == todo.reviewer_id)):
        raise HTTPException(
            status_code=403, detail="only the reviewer or an admin can dismiss a to-do"
        )
    try:
        core.dismiss(todo, payload.reason, name or ("Admin" if admin else None))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _read(todo, _periods(session, [todo]), None, oid, admin)


@router.get("/todos/export")
def export_todos(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
    status: TodoStatus | Literal["all"] = "all",
    show: Show = "all",
) -> StreamingResponse:
    rows, _, _ = _query(session, request, claims, status, show, None)
    periods = _periods(session, rows)
    buf = io.StringIO()
    w = safe_writer(buf)
    w.writerow(
        [
            "status",
            "overdue",
            "action",
            "who",
            "type",
            "role",
            "place",
            "review",
            "decided_by",
            "note",
            "advice_rule",
            "due",
            "done",
            "dismissed_reason",
            "commands",
        ]
    )
    for t in rows:
        w.writerow(
            [
                t.status,
                "yes" if core.is_overdue(t) else "",
                t.action,
                t.principal_name or t.principal_id,
                t.principal_type or "",
                t.role_name or "",
                t.scope_name or t.scope,
                periods.get(t.round_id, ""),
                t.decided_by_name or "",
                t.note or "",
                t.advice_rule or "",
                t.due_at.date().isoformat(),
                t.done_at.date().isoformat() if t.done_at else "",
                t.dismissed_reason or "",
                " && ".join(t.commands or []),
            ]
        )
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="rbaclens-todos.csv"'},
    )


@router.get("/todos/script", response_class=PlainTextResponse)
def todo_script(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
    show: Show = "all",
) -> str:
    """The open remove to-dos as one shell script to read, then run. RBAC Lens never runs it."""
    rows, _, _ = _query(session, request, claims, "open", show, None)
    lines = [
        "#!/bin/sh",
        "# RBAC Lens: grants to remove, decided in monthly reviews. Read before running.",
        "set -e",
        "",
    ]
    for t in rows:
        if t.action != "remove":
            continue
        lines.append(
            "# "
            + comment_text(
                f"{t.principal_name or t.principal_id} · {t.role_name or 'role'} "
                f"on {t.scope_name or t.scope}"
            )
        )
        lines.extend(t.commands or [])
    return "\n".join(lines) + "\n"
