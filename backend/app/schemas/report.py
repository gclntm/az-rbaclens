"""The assessment report (core/report.py), mapped to ISO/IEC 27001:2022 Annex A."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.advice import Weight

ControlStatus = Literal["met", "partly", "not_met"]


class ReportControl(BaseModel):
    control: str  # "8.2"
    name: str  # "Privileged access rights"
    status: ControlStatus
    # One sentence per fact the status rests on.
    evidence: list[str]


class ReportGrant(BaseModel):
    """One grant in a report table."""

    grant_id: str
    principal_id: str
    principal_name: str | None
    kind: str  # person | group | app | other
    role_name: str | None
    level: str = "unknown"  # what the role allows: access | keys | change | data | view
    scope: str
    scope_name: str | None
    scope_level: str
    how: str  # standing | pim | time-bound
    advice_rule: str
    advice_title: str
    advice_weight: Weight


class ReportRecommendation(BaseModel):
    rule: str
    weight: Weight
    title: str
    sentence: str  # "7 standing grants can change who has access high up; make them PIM-eligible."
    count: int
    controls: list[str]
    grants: list[ReportGrant]  # the first few, broadest first


class ReportReviews(BaseModel):
    subscriptions: int
    subscriptions_with_responsible: int
    without_responsible: list[str]  # subscription names
    rounds_this_period: int
    items_this_period: int
    decided_this_period: int
    # Owners of apps and groups, and app secrets, reviewed like grants (0045).
    owners_this_period: int = 0
    owners_decided: int = 0
    secrets_this_period: int = 0
    secrets_decided: int = 0
    last_period: str | None
    last_period_on_time: bool | None  # None: no earlier round
    todos_open: int
    todos_overdue: int
    todos_done: int
    todos_avg_days_to_close: float | None


class ReportChanges(BaseModel):
    days: int
    given: int
    removed: int
    powerful_given: int
    unexplained: int  # R12
    check_age_hours: float | None
    subscriptions_collected_hourly: int
    activity_gap_hours: float | None  # the oldest "newest pull" among subscriptions
    subscriptions_not_collected: list[str]


class Report(BaseModel):
    scope_id: str | None  # None = the whole tenant
    scope_name: str
    scope_level: str | None
    generated_at: datetime
    access_as_of: datetime | None
    identities: int
    grants: int
    summary: list[str]  # the five things that matter most, as sentences
    controls: list[ReportControl]
    privileged: list[
        ReportGrant
    ]  # change access or keys, on Entra ID, a management group or subscription
    break_glass: list[str]
    microsoft_apps: int  # grants held by Microsoft's own apps (not counted as privileged risk)
    identities_section: dict[str, list[ReportGrant]]  # orphans, guests, unknown_members
    least_privilege: list[ReportRecommendation]
    reviews: ReportReviews
    changes: ReportChanges
    recommendations: list[ReportRecommendation]
    # Key numbers, for saving and comparing assessments.
    numbers: dict[str, float | int | None] = Field(default_factory=dict)


class AssessmentRead(BaseModel):
    id: int
    scope_id: str | None
    scope_name: str
    created_at: datetime
    created_by_name: str | None
    access_as_of: datetime | None
    note: str | None = None
    statuses: dict[str, ControlStatus]
    numbers: dict[str, float | int | None]


class AssessmentWrite(BaseModel):
    scope: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=2000)
