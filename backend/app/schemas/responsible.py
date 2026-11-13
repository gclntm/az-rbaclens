"""Payloads of the responsible-people endpoints and ``GET /me``."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.iam_access import ScopeLevel


class PersonRead(BaseModel):
    person_id: str
    person_name: str | None
    person_email: str | None


class ResponsibleRead(PersonRead):
    source: Literal["manual", "tag"]
    set_by_name: str | None
    set_at: datetime


class ResponsibleNode(BaseModel):
    """One place in the tree, with who is responsible and where that comes from."""

    scope_id: str
    name: str | None
    level: ScopeLevel
    parent_id: str | None
    depth: int
    responsible: ResponsibleRead | None
    # The place the setting is on; equals ``scope_id`` when set here.
    from_scope_id: str | None
    from_scope_name: str | None


class ResponsibleOf(BaseModel):
    scope_id: str
    responsible: ResponsibleRead | None
    from_scope_id: str | None
    from_scope_name: str | None


class ResponsibleWrite(BaseModel):
    # Email, user principal name, or Entra object id of a user.
    person: str = Field(min_length=3, max_length=320)


class SuggestionRead(BaseModel):
    scope_id: str
    scope_name: str | None
    level: ScopeLevel
    tag_value: str
    person_id: str | None
    person_name: str | None
    person_email: str | None
    status: Literal["open", "unmatched", "ignored", "used"]
    # Who is responsible there now (set here or above), if anyone.
    current_name: str | None


class Suggestions(BaseModel):
    tag_name: str
    open: list[SuggestionRead]
    unmatched: list[SuggestionRead]
    ignored: list[SuggestionRead]


class SuggestionDecision(BaseModel):
    scope: str = Field(min_length=1, max_length=1000)


class ResponsibleSettings(BaseModel):
    tag_name: str = Field(min_length=1, max_length=512)


class MyPlace(BaseModel):
    scope_id: str
    name: str | None
    level: ScopeLevel


class Me(BaseModel):
    """Who is using the app. With sign-in off everyone is an admin, unnamed."""

    signed_in: bool
    oid: str | None
    name: str | None
    is_admin: bool
    responsible_for: list[MyPlace]
