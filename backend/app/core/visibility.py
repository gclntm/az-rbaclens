"""What a signed-in person who is not an admin may see.

Admins, and everyone while sign-in is off, see everything. Anyone else sees:

- the places they are responsible for, and everything inside them;
- the grants that apply there, including grants given above (who can act
  on their place), and the identities holding them;
- their own access, directly or through their groups;
- the reviews they do, or that cover their places.

:func:`guard` runs on every signed-in route. It refuses anything not on
the allow-list (deny by default), checks the place, identity, grant or
review a request names, and leaves a :class:`Visibility` on
``request.state.vis`` so list endpoints can filter. ``None`` there means
"sees everything".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.current_view import CurrentView, current_view, owner_of
from app.core.responsible import places_of, resolve_all
from app.core.security import require_auth, who_is
from app.db.models import ReviewRound, Todo
from app.db.session import get_session

FORBIDDEN = "not available to you: you see the places you are responsible for and your own access"


@dataclass
class Visibility:
    oid: str
    places: set[str]  # every place inside a place where the person is set
    known: set[str]  # every registered place
    parents: dict[str, str | None]
    view: CurrentView
    my_ids: set[str] = field(default_factory=set)  # the person and their groups

    def sees_place(self, scope: str) -> bool:
        """A place page: the person's places and everything inside."""
        return owner_of(scope.lower(), self.known) in self.places

    @cached_property
    def above(self) -> set[str]:
        """Places above the person's places (grants given there apply to them)."""
        out: set[str] = set()
        for sid in self.places:
            cur = self.parents.get(sid)
            while cur and cur not in out:
                out.add(cur)
                cur = self.parents.get(cur)
        return out

    def sees_grant_at(self, scope: str) -> bool:
        """A grant applies to one of the person's places: given inside it or above it."""
        s = scope.lower()
        return self.sees_place(s) or s in self.above

    def sees_grant(self, scope: str, principal_id: str) -> bool:
        return principal_id.lower() in self.my_ids or self.sees_grant_at(scope)

    @cached_property
    def principals(self) -> set[str]:
        """Identities whose access applies to the person's places, plus the person and their groups."""
        return {
            ra.principal_id.lower() for ra in self.view.ras if self.sees_grant_at(ra.scope)
        } | self.my_ids

    def sees_principal(self, principal_id: str) -> bool:
        return principal_id.lower() in self.principals

    def sees_round(self, rnd: ReviewRound) -> bool:
        return rnd.reviewer_id == self.oid or (
            rnd.scope_id is not None and self.sees_place(rnd.scope_id)
        )


def compute(session: Session, oid: str) -> Visibility:
    """Places = everything inside each place where the person is set, even
    parts someone else reviews (they still sit inside the person's place)."""
    _, scopes = resolve_all(session)
    view = current_view(session)
    groups = view.groups_of({oid}).get(oid, set())
    places: set[str] = set()
    for sid in places_of(session, oid):
        places |= view.subtree(sid)
    return Visibility(
        oid=oid,
        places=places,
        known=set(scopes),
        parents={sid: meta[2] for sid, meta in scopes.items()},
        view=view,
        my_ids={oid} | groups,
    )


def vis_of(request: Request) -> Visibility | None:
    """The request's visibility; ``None`` = sees everything."""
    return getattr(request.state, "vis", None)


# ---- the guard ---------------------------------------------------------------------------

_PLACE_QUERY = re.compile(
    r"^/api/(scopes/(assignments|capabilities|findings|history|activity)|responsible/of)$"
)
_LISTS = re.compile(
    r"^/api/(me|freshness|scopes/tree|principals|changes|findings|search|responsible/tree|reviews"
    r"|todos(/summary|/export|/script)?|report(/annex|/assessments)?|iam-access/role-definitions(/[^/]+)?)$"
)
_PLACE_PATH = re.compile(r"^/api/scopes/(?P<scope>.+)$")
_PRINCIPAL = re.compile(r"^/api/principals/(?P<pid>[^/]+)$")
_OWN_ACTIVITY = re.compile(r"^/api/principals/(?P<pid>[^/]+)/activity$")
_GRANT = re.compile(r"^/api/grants/(?P<gid>[^/]+)$")
_ROUND = re.compile(r"^/api/reviews/(?P<rid>\d+)(/(export|finish|accept-advice|items/\d+))?$")
_TODO = re.compile(r"^/api/todos/(?P<tid>\d+)/dismiss$")
_AI = re.compile(r"^/api/ai/(status|preview|explain)$")  # the endpoint checks the grant or identity


def _deny() -> None:
    raise HTTPException(status_code=403, detail=FORBIDDEN)


def guard(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Annotated[dict[str, Any] | None, Depends(require_auth)] = None,
) -> None:
    """Restrict a signed-in person who is not an admin (no-op otherwise)."""
    request.state.vis = None
    if claims is None:
        return
    oid, _, admin = who_is(session, claims)
    if admin:
        return
    if not oid:
        _deny()
    vis = compute(session, oid)
    request.state.vis = vis
    path, method = request.url.path.rstrip("/"), request.method

    if method == "GET" and _LISTS.match(path):
        scope = request.query_params.get("scope")
        if scope and not vis.sees_place("/" + scope.strip().strip("/")):
            _deny()
        return
    if method == "GET" and _PLACE_QUERY.match(path):
        if not vis.sees_place("/" + request.query_params.get("scope", "").strip().strip("/")):
            _deny()
        return
    if method == "GET" and (m := _PLACE_PATH.match(path)):
        if not vis.sees_place("/" + m["scope"]):
            _deny()
        return
    if method == "GET" and (m := _PRINCIPAL.match(path)):
        if not vis.sees_principal(m["pid"]):
            _deny()
        return
    if method == "GET" and (m := _OWN_ACTIVITY.match(path)):
        if m["pid"].lower() not in vis.my_ids:
            _deny()  # what someone did anywhere in Azure is not a place's business
        return
    if method == "GET" and (m := _GRANT.match(path)):
        ra = vis.view.by_grant_id.get(m["gid"].lower())
        if ra is not None and not vis.sees_grant(ra.scope, ra.principal_id):
            _deny()
        return
    if (m := _ROUND.match(path)) and (
        method == "GET" or (method in ("PUT", "POST") and m.group(3))
    ):
        rnd = session.get(ReviewRound, int(m["rid"]))
        if rnd is not None and not vis.sees_round(rnd):
            _deny()
        return
    if _AI.match(path) and (method == "GET" or (method == "POST" and not path.endswith("/status"))):
        return
    if method == "POST" and (m := _TODO.match(path)):
        todo = session.get(Todo, int(m["tid"]))
        if todo is not None and todo.reviewer_id != vis.oid and not vis.sees_grant_at(todo.scope):
            _deny()
        return
    _deny()
