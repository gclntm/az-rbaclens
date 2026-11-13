"""Monthly reviews: rounds, items, Keep / Remove / Ask, finish, export.

A round's reviewer (or an admin) decides; anyone signed in can read.
Every change is recorded by the audit middleware.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import reviews as core
from app.core.csv_safe import safe_writer
from app.core.security import require_admin, require_auth, who_is
from app.core.todos import create_for_round as create_todos
from app.core.visibility import vis_of
from app.db.models import ReviewItem, ReviewRound, Scope
from app.db.session import get_session
from app.schemas.reviews import (
    AcceptAdviceResult,
    ReviewDecisionWrite,
    ReviewItemRead,
    ReviewProgress,
    ReviewRoundDetail,
    ReviewRoundRead,
)

router = APIRouter(tags=["reviews"])

Claims = Annotated[dict[str, Any] | None, Depends(require_auth)]


def _items(session: Session, round_id: int) -> list[ReviewItem]:
    return list(
        session.scalars(
            select(ReviewItem).where(ReviewItem.round_id == round_id).order_by(ReviewItem.id)
        ).all()
    )


def _can_decide(rnd: ReviewRound, oid: str | None, is_admin: bool) -> bool:
    return rnd.finished_at is None and (is_admin or (oid is not None and oid == rnd.reviewer_id))


def _round(
    session: Session, rnd: ReviewRound, items: list[ReviewItem], oid: str | None, admin: bool
) -> dict:
    scope = session.get(Scope, rnd.scope_id) if rnd.scope_id else None
    p = core.progress(items)
    return {
        "id": rnd.id,
        "scope_id": rnd.scope_id,
        "scope_name": scope.name if scope else None,
        "scope_level": scope.level if scope else None,
        "period": rnd.period,
        "reviewer_id": rnd.reviewer_id,
        "reviewer_name": rnd.reviewer_name,
        "opened_at": rnd.opened_at,
        "due_at": rnd.due_at,
        "finished_at": rnd.finished_at,
        "finished_by_name": rnd.finished_by_name,
        "progress": ReviewProgress(**p.__dict__),
        "blocking": core.blocking(items),
        "can_decide": _can_decide(rnd, oid, admin),
    }


def _item(i: ReviewItem, names: dict[str, str | None]) -> ReviewItemRead:
    return ReviewItemRead(
        id=i.id,
        grant_id=i.grant_fingerprint,
        scope=i.scope,
        scope_name=i.scope_name,
        scope_level=i.scope_level,
        principal_id=i.principal_id,
        principal_name=i.principal_name,
        principal_type=i.principal_type,
        role_definition_id=i.role_definition_id,
        role_name=i.role_name,
        level=i.level,
        moved_from_scope=i.moved_from_scope,
        moved_from_name=names.get(i.moved_from_scope) if i.moved_from_scope else None,
        decision=i.decision,
        note=i.note,
        decided_by_name=i.decided_by_name,
        decided_at=i.decided_at,
        suggested=i.suggested,
        suggested_rule=i.suggested_rule,
        suggested_title=i.suggested_title,
        carried=i.carried_from_round_id is not None,
        ask_remind_at=i.ask_remind_at,
        gone_at=i.gone_at,
        remove_command=core.remove_command(i),
    )


def _get(session: Session, round_id: int) -> ReviewRound:
    rnd = session.get(ReviewRound, round_id)
    if rnd is None:
        raise HTTPException(status_code=404, detail="review not found")
    return rnd


@router.get("/reviews", response_model=list[ReviewRoundRead])
def list_reviews(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
    mine: bool = False,
    period: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
) -> list[ReviewRoundRead]:
    """Newest month first; ``mine`` keeps the rounds you review (all of them with sign-in off)."""
    oid, _, admin = who_is(session, claims)
    stmt = select(ReviewRound).order_by(ReviewRound.period.desc(), ReviewRound.id)
    if period:
        stmt = stmt.where(ReviewRound.period == period)
    if mine and oid:
        stmt = stmt.where(ReviewRound.reviewer_id == oid)
    vis = vis_of(request)
    rounds = [r for r in session.scalars(stmt).all() if vis is None or vis.sees_round(r)]
    return [
        ReviewRoundRead(**_round(session, r, _items(session, r.id), oid, admin)) for r in rounds
    ]


@router.get("/reviews/{round_id}", response_model=ReviewRoundDetail)
def review_detail(
    round_id: int, session: Annotated[Session, Depends(get_session)], claims: Claims
) -> ReviewRoundDetail:
    oid, _, admin = who_is(session, claims)
    rnd = _get(session, round_id)
    items = _items(session, round_id)
    moved = {i.moved_from_scope for i in items if i.moved_from_scope}
    names = dict(
        session.execute(select(Scope.id, Scope.name).where(Scope.id.in_(moved or {""}))).all()
    )
    order = {None: 0, "ask": 1, "remove": 2, "change": 3, "keep": 4}
    items.sort(
        key=lambda i: (
            i.gone_at is not None,
            order.get(i.decision, 9),
            i.principal_name or i.principal_id,
        )
    )
    return ReviewRoundDetail(
        **_round(session, rnd, items, oid, admin), items=[_item(i, names) for i in items]
    )


@router.put("/reviews/{round_id}/items/{item_id}", response_model=ReviewItemRead)
def decide_item(
    round_id: int,
    item_id: int,
    payload: ReviewDecisionWrite,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
) -> ReviewItemRead:
    oid, name, admin = who_is(session, claims)
    rnd = _get(session, round_id)
    item = session.get(ReviewItem, item_id)
    if item is None or item.round_id != round_id:
        raise HTTPException(status_code=404, detail="item not found in this review")
    if rnd.finished_at is not None:
        raise HTTPException(status_code=409, detail="this review is finished")
    if not _can_decide(rnd, oid, admin):
        raise HTTPException(status_code=403, detail="only the reviewer or an admin can decide")
    try:
        core.decide(session, item, payload.decision, payload.note, oid, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _item(item, {})


@router.post("/reviews/{round_id}/accept-advice", response_model=AcceptAdviceResult)
def accept_advice(
    round_id: int, session: Annotated[Session, Depends(get_session)], claims: Claims
) -> AcceptAdviceResult:
    """Confirm the playbook's suggested decision on every item still to decide."""
    oid, name, admin = who_is(session, claims)
    rnd = _get(session, round_id)
    if not _can_decide(rnd, oid, admin):
        raise HTTPException(
            status_code=403,
            detail="only the reviewer or an admin can decide, before the review is finished",
        )
    counts = core.accept_advice(session, _items(session, round_id), oid, name)
    session.commit()
    return AcceptAdviceResult(**counts)


@router.post("/reviews/{round_id}/finish", response_model=ReviewRoundRead)
def finish_review(
    round_id: int, session: Annotated[Session, Depends(get_session)], claims: Claims
) -> ReviewRoundRead:
    oid, name, admin = who_is(session, claims)
    rnd = _get(session, round_id)
    if not _can_decide(rnd, oid, admin):
        raise HTTPException(
            status_code=403, detail="only the reviewer or an admin can finish, and only once"
        )
    items = _items(session, round_id)
    left = core.blocking(items)
    if left:
        raise HTTPException(status_code=409, detail=f"{left} still to decide or asked")
    rnd.finished_at = datetime.now(UTC)
    rnd.finished_by_name = name or ("Admin" if admin else None)
    create_todos(session, rnd, items, rnd.finished_at)
    session.commit()
    return ReviewRoundRead(**_round(session, rnd, items, oid, admin))


@router.get("/reviews/{round_id}/export")
def export_review(
    round_id: int, session: Annotated[Session, Depends(get_session)]
) -> StreamingResponse:
    """CSV of every item with its decision and, for "remove", the command to run."""
    rnd = _get(session, round_id)
    buf = io.StringIO()
    w = safe_writer(buf)
    w.writerow(
        [
            "place",
            "who",
            "type",
            "role",
            "level",
            "decision",
            "note",
            "decided_by",
            "removed_in_azure",
            "advice_rule",
            "command",
        ]
    )
    for i in _items(session, round_id):
        w.writerow(
            [
                i.scope_name or i.scope,
                i.principal_name or i.principal_id,
                i.principal_type or "",
                i.role_name or "",
                i.level,
                i.decision or "",
                i.note or "",
                i.decided_by_name or "",
                "yes" if i.gone_at else "",
                i.suggested_rule or "",
                core.remove_command(i) if i.decision == "remove" else "",
            ]
        )
    name = f"review-{rnd.period}-{rnd.id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/reviews/open", dependencies=[Depends(require_admin)])
def open_reviews(session: Annotated[Session, Depends(get_session)]) -> dict[str, int]:
    """Open this month's rounds now and bring their items up to date (also runs after every check)."""
    return core.open_and_sync(session)
