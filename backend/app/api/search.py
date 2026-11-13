"""``GET /search`` — the ⌘K palette: scopes, principals and roles by name or id."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.visibility import vis_of
from app.db.models import Principal, RoleDefinition, Scope
from app.db.session import get_session
from app.schemas.search import SearchHit, SearchResults

router = APIRouter(tags=["search"])


def _ranked(name_col, id_col, needle: str):
    """Exact name match, then prefix, then substring; shorter names first."""
    lowered = func.lower(func.coalesce(name_col, id_col))
    return (
        case(
            (lowered == needle, 0),
            (lowered.like(f"{needle}%"), 1),
            else_=2,
        ),
        func.length(func.coalesce(name_col, id_col)),
    )


@router.get("/search", response_model=SearchResults)
def search(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=25)] = 8,
) -> SearchResults:
    needle = q.strip().lower()
    if len(needle) < 2:
        return SearchResults(query=q, scopes=[], principals=[], roles=[])
    # LIKE wildcards in the query are matched literally.
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    def like(col):
        return func.lower(col).like(pattern, escape="\\")

    vis = vis_of(request)
    # Someone who is not an admin: look further, then keep what they may see.
    fetch = limit if vis is None else 200

    scopes = session.execute(
        select(Scope.id, Scope.name, Scope.level)
        .where(or_(like(Scope.name), like(Scope.id)))
        .order_by(*_ranked(Scope.name, Scope.id, needle), Scope.id)
        .limit(fetch)
    ).all()
    principals = session.execute(
        select(Principal.id, Principal.display_name, Principal.principal_type)
        .where(or_(like(Principal.display_name), like(Principal.id)))
        .order_by(*_ranked(Principal.display_name, Principal.id, needle), Principal.id)
        .limit(fetch)
    ).all()
    if vis is not None:
        scopes = [s for s in scopes if vis.sees_place(s[0])][:limit]
        principals = [p for p in principals if vis.sees_principal(p[0])][:limit]
    roles = session.execute(
        select(
            RoleDefinition.role_definition_id, RoleDefinition.role_name, RoleDefinition.role_type
        )
        .where(or_(like(RoleDefinition.role_name), like(RoleDefinition.role_definition_id)))
        .order_by(
            *_ranked(RoleDefinition.role_name, RoleDefinition.role_definition_id, needle),
            RoleDefinition.role_definition_id,
        )
        .limit(limit)
    ).all()
    return SearchResults(
        query=q,
        scopes=[SearchHit(id=i, name=n, kind=lvl) for i, n, lvl in scopes],
        principals=[SearchHit(id=i, name=n, kind=t) for i, n, t in principals],
        roles=[SearchHit(id=i, name=n, kind=t) for i, n, t in roles],
    )
