"""Principal page payloads (``app.api.principal_views``)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.activity import ActivityEventRead
from app.schemas.advice import IdentityAdvice
from app.schemas.findings import CurrentFindingRead
from app.schemas.iam_access import ScopeLevel
from app.schemas.registry import ConditionFields, GrantOrigin, PrincipalRead

AccessSource = Literal["direct", "group"]


class PrincipalAccessRow(ConditionFields, GrantOrigin):
    """One current RA (or PIM eligibility) giving this principal access, directly or via a group."""

    # Null for a PIM eligibility.
    assignment_id: int | None
    scan_id: int
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    # Subscription the scope sits in; null for management-group / tenant scopes.
    subscription_id: str | None
    subscription_name: str | None
    role_name: str | None
    role_definition_id: str
    role_type: str | None
    role_risk: str | None
    # What the role lets the holder do (app/core/capabilities.py).
    level: Literal["access", "keys", "change", "data", "view", "unknown"] = "unknown"
    # High-risk role at a management-group or subscription scope.
    is_high_risk: bool
    source: AccessSource
    # Set when ``source == "group"``: the group that holds the RA.
    via_group_id: str | None = None
    via_group_name: str | None = None
    # From the owning snapshot's coverage cache. For a group-held RA these
    # describe the whole group's activity, not this principal's.
    observed_op_count: int | None = None
    coverage_pct: int | None = None
    open_findings: int = 0
    # The playbook's verdict for this grant (null for PIM eligibilities).
    advice_weight: Literal["must", "should", "could", "info"] | None = None
    advice_title: str | None = None
    # PIM: ``eligible`` = must be activated first (not a role assignment);
    # ``activated`` = an eligible role switched on; ``until`` = end date,
    # null = permanent / no end.
    eligible: bool = False
    activated: bool = False
    until: datetime | None = None


class PrincipalGroup(BaseModel):
    group_id: str
    group_name: str | None
    # Membership snapshot the link comes from.
    scan_id: int
    # Current RAs the group holds.
    assignment_count: int
    high_risk_count: int


class PrincipalMember(BaseModel):
    member_id: str
    member_name: str | None
    member_type: str | None


class PrincipalSummary(BaseModel):
    direct: int
    via_group: int
    high_risk: int
    open_findings: int
    subscriptions: int
    # PIM eligibilities (direct or via group); see ``PrincipalPage.eligible``.
    eligible: int = 0


class PrincipalPage(BaseModel):
    principal: PrincipalRead
    summary: PrincipalSummary
    assignments: list[PrincipalAccessRow]
    # Roles the principal can activate through PIM, same row shape.
    eligible: list[PrincipalAccessRow] = []
    groups: list[PrincipalGroup]
    # Groups only: transitive members from the group's latest membership
    # snapshot (capped at ``members_limit``); null for users / SPs.
    members: list[PrincipalMember] | None
    members_total: int
    # Groups only: whether a check could read the members from Microsoft Graph at all.
    # False with no members = unreadable (permission); True with none = the group is empty.
    members_read: bool = False
    # Findings on every RA in ``assignments`` (direct and via group).
    findings: list[CurrentFindingRead]
    # "What to do": the identity's own grants, grouped by rule (core/advice.py).
    advice: IdentityAdvice | None = None


class OperationCount(BaseModel):
    operation: str
    # ``operation`` in words (core/operation_names.py).
    display: str | None = None
    count: int
    newest_at: datetime | None


class SubscriptionCount(BaseModel):
    subscription_id: str
    subscription_name: str | None
    count: int
    newest_at: datetime | None


class PrincipalActivity(BaseModel):
    """The principal's own Activity Log events (by JWT object id).

    For a group: events by any of its members, since groups never call ARM.
    """

    principal_id: str
    window_days: int
    # Identities whose events are counted: the principal, or a group's members.
    identities: int
    event_count: int
    # Distinct operation names (``top_operations`` holds the top 15).
    operation_count: int
    newest_at: datetime | None
    # Raw ``caller`` strings seen (UPN or app id), most frequent first.
    callers: list[str]
    by_subscription: list[SubscriptionCount]
    top_operations: list[OperationCount]
    failed_count: int
    recent: list[ActivityEventRead]
