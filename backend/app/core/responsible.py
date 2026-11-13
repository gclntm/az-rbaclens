"""Responsible people: who reviews each place.

Set in RBAC Lens by an admin, on any place; the setting applies to
everything inside the place unless someone else is set lower down. This
is not the Azure Owner role and gives no access.

Suggestions come from a tag on subscriptions and resource groups
(``owner`` by default, a setting): a value that is the email of exactly
one Entra ID user becomes a suggestion an admin can use or ignore;
anything else is listed as "could not match".
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AppSetting, Responsibility, ResponsibleSuggestion, Scope
from app.tools.graph_resolver import EntraUser, looks_like_email

logger = logging.getLogger(__name__)

SETTINGS_KEY = "responsible"
DEFAULT_TAG = "owner"
TAGGED_LEVELS = ("subscription", "resource_group")
# Azure tag names: up to 512 characters, none of < > % & \ ? /
_TAG_NAME = re.compile(r"^[^<>%&\\?/]{1,512}$")

Lookup = Callable[[str], EntraUser | None]


# ---- settings ----------------------------------------------------------------------------


def tag_name(session: Session) -> str:
    row = session.get(AppSetting, SETTINGS_KEY)
    return (row.value or {}).get("tag_name") or DEFAULT_TAG if row else DEFAULT_TAG


def set_tag_name(session: Session, name: str) -> str:
    """Validate and store the tag name. Raises ``ValueError``. The caller commits."""
    name = name.strip()
    if not _TAG_NAME.match(name):
        raise ValueError("a tag name is 1 to 512 characters, without < > % & \\ ? /")
    row = session.get(AppSetting, SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=SETTINGS_KEY, value={"tag_name": name}))
    else:
        row.value = {**(row.value or {}), "tag_name": name}
    return name


# ---- who is responsible --------------------------------------------------------------------


@dataclass(frozen=True)
class Resolved:
    """The responsible person for a place and the place the setting comes from."""

    person: Responsibility
    from_scope: str


def resolve_all(session: Session) -> tuple[dict[str, Resolved], dict[str, Any]]:
    """``({scope id: Resolved}, {scope id: Scope row tuple})`` for every known place.

    Walks the tree top-down once, so the nearest setting above wins.
    """
    scopes = {
        sid: (name, level, parent)
        for sid, name, level, parent in session.execute(
            select(Scope.id, Scope.name, Scope.level, Scope.parent_id)
        ).all()
    }
    set_here = {r.scope_id: r for r in session.scalars(select(Responsibility)).all()}
    children: dict[str | None, list[str]] = defaultdict(list)
    for sid, (_, _, parent) in scopes.items():
        children[parent if parent in scopes else None].append(sid)
    out: dict[str, Resolved] = {}
    stack: list[tuple[str, Resolved | None]] = [(sid, None) for sid in children[None]]
    while stack:
        sid, inherited = stack.pop()
        here = Resolved(set_here[sid], sid) if sid in set_here else inherited
        if here is not None:
            out[sid] = here
        stack.extend((c, here) for c in children.get(sid, ()))
    return out, scopes


def responsible_for(session: Session, scope_id: str) -> Resolved | None:
    """The nearest setting at or above ``scope_id`` (by walking parents)."""
    cur: str | None = scope_id
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        row = session.get(Responsibility, cur)
        if row is not None:
            return Resolved(row, cur)
        cur = session.scalar(select(Scope.parent_id).where(Scope.id == cur))
    return None


def places_of(session: Session, person_id: str) -> list[str]:
    """Places where ``person_id`` is set (not counting what that covers below)."""
    return list(
        session.scalars(
            select(Responsibility.scope_id).where(Responsibility.person_id == person_id.lower())
        ).all()
    )


def set_responsible(
    session: Session, scope_id: str, user: EntraUser, source: str, actor: dict[str, Any] | None
) -> Responsibility:
    """Set (or replace) the person on a place. The caller commits."""
    row = session.get(Responsibility, scope_id)
    if row is None:
        row = Responsibility(scope_id=scope_id)
        session.add(row)
    row.person_id = user.id.lower()
    row.person_name = user.display_name
    row.person_email = user.email
    row.source = source
    row.set_by = (actor or {}).get("oid")
    row.set_by_name = (actor or {}).get("name") or (actor or {}).get("preferred_username")
    row.set_at = datetime.now(UTC)
    return row


# ---- tag suggestions -------------------------------------------------------------------------


def _tag_value(tags: dict[str, Any] | None, name: str) -> str | None:
    """Azure tag names are case-insensitive."""
    for k, v in (tags or {}).items():
        if str(k).lower() == name.lower():
            value = str(v).strip()
            return value or None
    return None


def refresh_suggestions(session: Session, lookup: Lookup, now: datetime | None = None) -> int:
    """Re-read the tag on every subscription and resource group. Commits.

    A decision (used / ignored) is kept while the tag value is unchanged;
    a new value is matched again. Places that lost the tag drop out.
    Returns the number of places with the tag.
    """
    now = now or datetime.now(UTC)
    name = tag_name(session)
    existing = {s.scope_id: s for s in session.scalars(select(ResponsibleSuggestion)).all()}
    cache: dict[str, EntraUser | None] = {}
    seen: set[str] = set()
    for sid, tags in session.execute(
        select(Scope.id, Scope.tags).where(Scope.level.in_(TAGGED_LEVELS))
    ).all():
        value = _tag_value(tags, name)
        if value is None:
            continue
        seen.add(sid)
        row = existing.get(sid)
        if row is not None and row.tag_value == value and row.status in ("used", "ignored"):
            row.checked_at = now
            continue
        key = value.lower()
        if key not in cache:
            cache[key] = lookup(value) if looks_like_email(value) else None
        user = cache[key]
        if row is None:
            row = ResponsibleSuggestion(scope_id=sid)
            session.add(row)
        row.tag_value = value
        row.person_id = user.id if user else None
        row.person_name = user.display_name if user else None
        row.person_email = user.email if user else None
        row.status = "open" if user else "unmatched"
        row.checked_at = now
    stale = set(existing) - seen
    if stale:
        session.execute(
            delete(ResponsibleSuggestion).where(ResponsibleSuggestion.scope_id.in_(stale))
        )
    session.commit()
    return len(seen)


def graph_lookup() -> Lookup:
    """A ``Lookup`` backed by Microsoft Graph with the app's credential."""
    from app.core.azure import get_credential
    from app.tools.graph_resolver import find_user

    credential = get_credential()
    return lambda value: find_user(value, credential)
