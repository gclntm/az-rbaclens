"""Payload of ``GET /grants/{grant_id}`` — one grant, in plain words."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.advice import Advice
from app.schemas.capabilities import CapabilityLevel, IdentityKind
from app.schemas.findings import CurrentFindingRead
from app.schemas.iam_access import ScopeLevel
from app.schemas.registry import ConditionFields, GrantOrigin, ScopeCrumb
from app.schemas.reviews import ReviewRef


class GrantPower(BaseModel):
    """One thing the role lets the identity do, or not."""

    key: str  # access | keys | change | data | view
    label: str
    allowed: bool
    # Extra words, e.g. which keys it can fetch, or "read only" for data.
    detail: str | None = None


class GrantOperation(BaseModel):
    operation: str
    display: str
    count: int


class GrantDetail(ConditionFields, GrantOrigin):
    assignment_id: int
    principal_id: str
    principal_name: str | None
    principal_type: str
    kind: IdentityKind
    # Groups: people and apps inside, from the newest check that read the group.
    member_count: int | None = None
    role_name: str | None
    role_definition_id: str
    role_type: str | None
    level: CapabilityLevel
    powers: list[GrantPower]
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    # Root first, ending with the parent of ``scope``.
    ancestors: list[ScopeCrumb]
    # Places inside ``scope`` the grant also applies to, as far as the registry knows them.
    inside_count: int
    activated: bool = False
    until: datetime | None = None
    # When the daily check that saw this grant finished.
    access_as_of: datetime | None = None
    # Newest Activity Log event by this identity, anywhere (reads are never logged).
    last_activity_at: datetime | None = None
    # From the check's coverage: changes this identity made under the grant's
    # role at its place, in the coverage window.
    window_days: int | None = None
    observed_op_count: int | None = None
    top_operations: list[GrantOperation] = Field(default_factory=list)
    findings: list[CurrentFindingRead] = Field(default_factory=list)
    # This month's review item for the grant, when a round covers it.
    review: ReviewRef | None = None
    # What to do about it, from the playbook (core/advice.py).
    advice: Advice | None = None
