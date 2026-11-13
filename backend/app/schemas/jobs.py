from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobKind = Literal["snapshot", "activity_pull"]
JobStatus = Literal["pending", "running", "done", "failed"]


class ScheduleCreate(BaseModel):
    kind: JobKind
    # Snapshot: anything the scan form accepts. Pull: a subscription GUID.
    target: str = Field(min_length=1, max_length=500)
    every_hours: int = Field(ge=1, le=720)
    # Daily schedules (every_hours = 24) only: run at this hour, container local time.
    at_hour: int | None = Field(default=None, ge=0, le=23)
    enabled: bool = True
    # Pulls only: how far back the first run looks.
    initial_window_days: int = Field(default=90, ge=1, le=90)
    # Due on the next scheduler tick rather than one interval from now.
    start_now: bool = True

    @model_validator(mode="after")
    def _daily_hour(self) -> "ScheduleCreate":
        if self.at_hour is not None and self.every_hours != 24:
            raise ValueError("at_hour needs every_hours = 24")
        return self


class ScheduleUpdate(BaseModel):
    enabled: bool | None = None
    every_hours: int | None = Field(default=None, ge=1, le=720)
    # -1 clears the hour (back to a plain interval).
    at_hour: int | None = Field(default=None, ge=-1, le=23)
    initial_window_days: int | None = Field(default=None, ge=1, le=90)


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: JobKind
    target: str
    every_hours: int
    at_hour: int | None = None
    enabled: bool
    initial_window_days: int
    next_run_at: datetime
    last_run_at: datetime | None
    last_job_id: int | None
    last_job_status: JobStatus | None = None
    # Why the last run failed (kept until one succeeds), or why it could not start.
    last_error: str | None
    # The same in one plain sentence (core/job_errors.py).
    last_error_summary: str | None = None
    # When it was switched off and by whom (empty name while sign-in is off).
    paused_at: datetime | None = None
    paused_by: str | None = None
    # Failed runs in a row; 0 when the last one worked.
    failures: int = 0
    # Pulls only: the Activity Log is collected up to here by this schedule.
    covered_until: datetime | None = None
    created_at: datetime


class SecondPassRead(BaseModel):
    """The newest second pass of one subscription."""

    status: JobStatus
    finished_at: datetime | None
    late_events: int


class SubscriptionCoverageRead(BaseModel):
    """How far one subscription's Activity Log is collected (core/coverage.py)."""

    subscription_id: str
    name: str
    schedule_id: int | None
    enabled: bool
    collecting: bool
    covered_until: datetime | None
    behind_hours: float | None
    # Days before the first uncollected event leaves Azure's 90 days; negative = already lost.
    days_left: int | None
    # ok | behind | at_risk | lost | never | no_schedule
    state: str
    second_pass: SecondPassRead | None = None


class CoverageRead(BaseModel):
    azure_keeps_days: int
    at_risk_days_left: int
    subscriptions: list[SubscriptionCoverageRead]
    # The nightly second pass: when the newest one finished, how many
    # subscriptions it covers, and the events it found that arrived late.
    second_pass_hours: int
    second_pass_finished_at: datetime | None
    second_pass_subscriptions: int
    second_pass_late_events: int
    second_pass_failed: int


class JobRead(BaseModel):
    """A scan or an activity pull, in one shape for the Jobs list."""

    kind: JobKind
    id: int
    # Scan scope, or the pulled subscription id.
    target: str
    status: JobStatus
    progress_message: str | None
    error: str | None
    # The error in one plain sentence (core/job_errors.py).
    error_summary: str | None = None
    # "schedule" or "manual"; null for runs from before this was recorded.
    origin: str | None = None
    schedule_id: int | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Checks only: how many places the identity was refused on (their grants are unknown).
    unread: int = 0
    # Pulls only.
    window_start: datetime | None = None
    window_end: datetime | None = None
    events_persisted: int | None = None
