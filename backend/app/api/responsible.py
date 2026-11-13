"""Responsible people (who reviews each place) and ``GET /me``.

Reads are open to every signed-in user; changes are admins only
(``require_admin``) and are recorded by the audit middleware.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.registry import normalise_scope_param
from app.core import responsible as core
from app.core.security import require_admin, require_auth, who_is
from app.core.visibility import vis_of
from app.db.models import Responsibility, ResponsibleSuggestion, Scope
from app.db.session import get_session
from app.schemas.responsible import (
    Me,
    MyPlace,
    ResponsibleNode,
    ResponsibleOf,
    ResponsibleRead,
    ResponsibleSettings,
    ResponsibleWrite,
    SuggestionDecision,
    SuggestionRead,
    Suggestions,
)

router = APIRouter(tags=["responsible"])

Admin = Annotated[dict[str, Any] | None, Depends(require_admin)]
_TREE_LEVELS = ("mgmt_group", "subscription", "resource_group")


def _read(r: Responsibility | None) -> ResponsibleRead | None:
    if r is None:
        return None
    return ResponsibleRead(
        person_id=r.person_id,
        person_name=r.person_name,
        person_email=r.person_email,
        source=r.source,
        set_by_name=r.set_by_name,
        set_at=r.set_at,
    )


def _scope(session: Session, raw: str) -> Scope:
    row = session.get(Scope, normalise_scope_param(raw))
    if row is None:
        raise HTTPException(
            status_code=404, detail="this place is not known to any completed check"
        )
    return row


def _refresh_in_background() -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        core.refresh_suggestions(session, core.graph_lookup())


@router.get("/responsible/tree", response_model=list[ResponsibleNode])
def responsible_tree(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> list[ResponsibleNode]:
    """Management groups, subscriptions and resource groups in tree order."""
    resolved, scopes = core.resolve_all(session)
    children: dict[str | None, list[str]] = {}
    for sid, (_name, level, parent) in scopes.items():
        if level in _TREE_LEVELS:
            children.setdefault(parent if parent in scopes else None, []).append(sid)
    for kids in children.values():
        kids.sort(key=lambda s: (scopes[s][0] or s).lower())
    out: list[ResponsibleNode] = []

    def walk(sid: str, depth: int) -> None:
        name, level, parent = scopes[sid]
        r = resolved.get(sid)
        out.append(
            ResponsibleNode(
                scope_id=sid,
                name=name,
                level=level,
                parent_id=parent,
                depth=depth,
                responsible=_read(r.person) if r else None,
                from_scope_id=r.from_scope if r else None,
                from_scope_name=scopes[r.from_scope][0] if r else None,
            )
        )
        for c in children.get(sid, ()):
            walk(c, depth + 1)

    for root in children.get(None, ()):
        walk(root, 0)
    vis = vis_of(request)
    if vis is not None:
        out = [n for n in out if vis.sees_place(n.scope_id) or n.scope_id in vis.above]
    return out


@router.get("/responsible/of", response_model=ResponsibleOf)
def responsible_of(
    session: Annotated[Session, Depends(get_session)], scope: str = Query(..., max_length=1000)
) -> ResponsibleOf:
    row = _scope(session, scope)
    r = core.responsible_for(session, row.id)
    from_name = session.scalar(select(Scope.name).where(Scope.id == r.from_scope)) if r else None
    return ResponsibleOf(
        scope_id=row.id,
        responsible=_read(r.person) if r else None,
        from_scope_id=r.from_scope if r else None,
        from_scope_name=from_name,
    )


@router.put("/responsible", response_model=ResponsibleOf)
def set_responsible(
    payload: ResponsibleWrite,
    admin: Admin,
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(..., max_length=1000),
) -> ResponsibleOf:
    """Set who is responsible for a place. The person must be an Entra ID user."""
    row = _scope(session, scope)
    user = core.graph_lookup()(payload.person)
    if user is None:
        raise HTTPException(status_code=400, detail="no Entra ID user with this email or id")
    core.set_responsible(session, row.id, user, "manual", admin)
    session.commit()
    return responsible_of(session, row.id)


@router.delete("/responsible", status_code=204)
def clear_responsible(
    admin: Admin,
    session: Annotated[Session, Depends(get_session)],
    scope: str = Query(..., max_length=1000),
) -> None:
    row = _scope(session, scope)
    existing = session.get(Responsibility, row.id)
    if existing is None:
        raise HTTPException(status_code=404, detail="nobody is set on this place itself")
    session.delete(existing)
    session.commit()


@router.get("/responsible/suggestions", response_model=Suggestions)
def suggestions(session: Annotated[Session, Depends(get_session)]) -> Suggestions:
    resolved, scopes = core.resolve_all(session)
    rows = session.scalars(
        select(ResponsibleSuggestion).order_by(ResponsibleSuggestion.scope_id)
    ).all()
    by_status: dict[str, list[SuggestionRead]] = {"open": [], "unmatched": [], "ignored": []}
    for s in rows:
        if s.status not in by_status or s.scope_id not in scopes:
            continue
        r = resolved.get(s.scope_id)
        if (
            s.status == "open"
            and r
            and r.from_scope == s.scope_id
            and r.person.person_id == s.person_id
        ):
            continue  # already set here to the suggested person
        name, level, _ = scopes[s.scope_id]
        by_status[s.status].append(
            SuggestionRead(
                scope_id=s.scope_id,
                scope_name=name,
                level=level,
                tag_value=s.tag_value,
                person_id=s.person_id,
                person_name=s.person_name,
                person_email=s.person_email,
                status=s.status,
                current_name=r.person.person_name if r else None,
            )
        )
    return Suggestions(tag_name=core.tag_name(session), **by_status)


@router.post("/responsible/suggestions/use", response_model=ResponsibleOf)
def use_suggestion(
    payload: SuggestionDecision, admin: Admin, session: Annotated[Session, Depends(get_session)]
) -> ResponsibleOf:
    row = _scope(session, payload.scope)
    s = session.get(ResponsibleSuggestion, row.id)
    if s is None or s.status != "open" or not s.person_id:
        raise HTTPException(status_code=409, detail="no open suggestion for this place")
    core.set_responsible(
        session, row.id, core.EntraUser(s.person_id, s.person_name, s.person_email), "tag", admin
    )
    s.status = "used"
    session.commit()
    return responsible_of(session, row.id)


@router.post("/responsible/suggestions/ignore", status_code=204)
def ignore_suggestion(
    payload: SuggestionDecision, admin: Admin, session: Annotated[Session, Depends(get_session)]
) -> None:
    row = _scope(session, payload.scope)
    s = session.get(ResponsibleSuggestion, row.id)
    if s is None or s.status not in ("open", "unmatched"):
        raise HTTPException(status_code=409, detail="no suggestion to ignore for this place")
    s.status = "ignored"
    session.commit()


@router.get("/responsible/settings", response_model=ResponsibleSettings)
def get_settings(session: Annotated[Session, Depends(get_session)]) -> ResponsibleSettings:
    return ResponsibleSettings(tag_name=core.tag_name(session))


@router.put("/responsible/settings", response_model=ResponsibleSettings)
def put_settings(
    payload: ResponsibleSettings,
    admin: Admin,
    background: BackgroundTasks,
    session: Annotated[Session, Depends(get_session)],
) -> ResponsibleSettings:
    """Change the tag name; suggestions are read again in the background."""
    try:
        name = core.set_tag_name(session, payload.tag_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    background.add_task(_refresh_in_background)
    return ResponsibleSettings(tag_name=name)


@router.get("/me", response_model=Me)
def me(
    session: Annotated[Session, Depends(get_session)],
    claims: Annotated[dict[str, Any] | None, Depends(require_auth)] = None,
) -> Me:
    if claims is None:
        return Me(signed_in=False, oid=None, name=None, is_admin=True, responsible_for=[])
    oid, name, is_admin = who_is(session, claims)
    places: list[MyPlace] = []
    if oid:
        for sid in core.places_of(session, oid):
            scope = session.get(Scope, sid)
            if scope is not None:
                places.append(MyPlace(scope_id=sid, name=scope.name, level=scope.level))
    return Me(signed_in=True, oid=oid, name=name, is_admin=is_admin, responsible_for=places)
