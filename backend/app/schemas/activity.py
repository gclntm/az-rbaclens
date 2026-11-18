import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.activity_targets import ENTRA_AUDIT, MGMT_GROUPS
from app.core.timeutil import as_utc

PullStatus = Literal["pending", "running", "done", "failed"]

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# A window may end a little after "now": the caller's clock, not ours.
_CLOCK_SLACK = timedelta(minutes=5)


def _subscription(value: str) -> str:
    """A subscription GUID, lowercased: events and the in-flight guard key on that form.
    ``management-groups`` stands for the logs of all management groups,
    ``entra-directory`` for the Entra ID audit log."""
    value = value.strip().lower()
    if value not in (MGMT_GROUPS, ENTRA_AUDIT) and not _GUID.match(value):
        raise ValueError(
            "subscription_id must be a subscription GUID, management-groups or entra-directory"
        )
    return value


class _Window(BaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def _not_in_the_future(self) -> "_Window":
        if self.end > datetime.now(UTC) + _CLOCK_SLACK:
            raise ValueError("end cannot be in the future")
        return self


class ActivityPullCreate(_Window):
    """Start a new Activity Log pull.

    ``start`` and ``end`` define the time window queried (Azure
    retains ~90 days). Both required to keep the call deterministic
    — the UI computes sensible defaults (e.g. last 7 days) and sends
    explicit values.
    """

    subscription_id: str = Field(min_length=8, max_length=64)

    @field_validator("subscription_id")
    @classmethod
    def _guid(cls, value: str) -> str:
        return _subscription(value)


class ActivityPullRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_id: str
    start_time: datetime
    end_time: datetime
    status: PullStatus
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    events_seen: int
    events_persisted: int
    progress_done: int
    progress_message: str | None
    created_at: datetime


class ActivityEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_id: str
    event_data_id: str
    caller: str | None
    caller_type: str | None
    operation_name: str
    # The operation in words (core/operation_names.py); filled by the API.
    operation_display: str | None = None
    resource_id: str | None
    resource_type: str | None
    status: str | None
    correlation_id: str | None
    occurred_at: datetime
    # How many events the row stands for: more than 1 for a daily row of machine
    # activity (core/activity_compaction.py), with ``first_occurred_at`` the first
    # of them and ``occurred_at`` the last.
    event_count: int = 1
    first_occurred_at: datetime | None = None
