"""Payloads of ``GET /changes`` and ``GET /freshness``."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ChangeKind = Literal[
    "granted", "removed", "eligible_added", "eligible_removed", "member_added", "member_removed"
]
ChangeSource = Literal["activity_log", "created_on", "daily_check", "entra_audit"]


class ChangeRead(BaseModel):
    id: int
    occurred_at: datetime
    kind: ChangeKind
    # How exact the time and author are: the Azure event, Azure's createdOn /
    # createdBy, the Entra ID audit log (group membership), or "first seen"
    # by a daily check (no author).
    source: ChangeSource
    scope: str
    scope_name: str | None
    scope_level: str
    principal_id: str
    principal_name: str | None
    principal_type: str | None
    role_definition_id: str | None
    role_name: str | None
    level: str
    via_group_id: str | None
    via_group_name: str | None
    actor_id: str | None
    actor_name: str | None
    # ``app`` when Entra records an app as the author (Teams, access packages, PIM, AD sync).
    actor_type: str | None = None
    # The nested group a member was added to or removed from (Entra ID audit log).
    direct_group_name: str | None = None
    # ``/grants/{grant_id}`` for grant changes; the grant may no longer exist.
    grant_id: str | None
    # The daily check that first saw it.
    check_id: int | None


class ChangeWeek(BaseModel):
    week_start: date
    given: int
    removed: int


class ChangesResponse(BaseModel):
    since: datetime
    until: datetime
    total: int
    given: int
    removed: int
    # Changes at levels access / keys.
    powerful: int
    weeks: list[ChangeWeek]
    items: list[ChangeRead]


class LastCheck(BaseModel):
    id: int
    status: str
    finished_at: datetime | None
    error: str | None


class StaleWarningRead(BaseModel):
    """One reason the data isn't kept up to date (core/staleness.py)."""

    kind: str
    text: str
    names: list[str] = []
    path: str
    # "danger" when data is being lost or about to be; "warn" otherwise.
    severity: str = "warn"


class Freshness(BaseModel):
    """How fresh the picture is, for the side menu footer."""

    # When the newest check behind the current picture finished.
    access_as_of: datetime | None
    # Newest Activity Log event collected.
    activity_up_to: datetime | None
    # The daily check of the whole tenant (the default snapshot schedule), if any.
    daily_check_at_hour: int | None
    # The time zone that hour is in (the backend's ``TZ``; "UTC" when unset).
    timezone: str = "UTC"
    next_check_at: datetime | None
    last_check: LastCheck | None
    # What keeps the data from being kept up to date; empty = all fine.
    warnings: list[StaleWarningRead] = []
