from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.timeutil import as_utc

Decision = Literal["reviewed", "accepted_risk", "snoozed", "resolved"]
FindingStatus = Literal["open", "reviewed", "accepted_risk", "snoozed", "resolved"]
Severity = Literal["high", "medium", "low"]


class ReviewDecisionWrite(BaseModel):
    decision: Decision
    note: str | None = Field(default=None, max_length=4000)
    # Only meaningful for ``snoozed``; the finding reads as ``open``
    # again once this instant has passed.
    snoozed_until: datetime | None = None


class ReviewDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fingerprint: str
    decision: Decision
    note: str | None
    snoozed_until: datetime | None
    actor: str | None
    actor_name: str | None
    decided_at: datetime


def finding_status(
    decision: ReviewDecisionRead | None, now: datetime | None = None
) -> FindingStatus:
    """Effective status: the decision, except an expired snooze reads as open."""
    if decision is None:
        return "open"
    if decision.decision == "snoozed":
        until = decision.snoozed_until
        if until is None:
            return "snoozed"
        return "snoozed" if as_utc(until) > (now or datetime.now(UTC)) else "open"
    return decision.decision


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_id: int
    role_assignment_id: int | None
    fingerprint: str
    category: str
    severity: Severity
    title: str
    detail: str
    suggestion: str | None
    evidence: dict[str, Any] | None
    computed_at: datetime
    status: FindingStatus = "open"
    decision: ReviewDecisionRead | None = None


class CurrentFindingRead(FindingRead):
    """A finding on a *current* RA, with the context a cross-scope list needs."""

    scope: str
    scope_name: str | None
    scope_level: str
    principal_id: str
    principal_name: str | None
    principal_type: str
    role_name: str | None


class BulkDecisionWrite(ReviewDecisionWrite):
    fingerprints: list[str] = Field(min_length=1, max_length=1000)


class BulkDecisionResult(BaseModel):
    updated: int


class FindingsSummary(BaseModel):
    total: int = 0
    open: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    # The grants these findings are on, and those with one open (global list only).
    grants: int | None = None
    open_grants: int | None = None


class FindingsResponse(BaseModel):
    scan_id: int
    # Null when findings have never been computed for this scan.
    computed_at: datetime | None
    window_days: int | None
    previous_scan_id: int | None = None
    summary: FindingsSummary
    findings: list[FindingRead]


class CurrentFindings(BaseModel):
    """``GET /findings`` — every finding on the current RA set, paged."""

    summary: FindingsSummary
    # Rows matching the filters, before paging.
    total: int
    offset: int
    limit: int
    findings: list[CurrentFindingRead]
