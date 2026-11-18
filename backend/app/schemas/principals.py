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


class OwnerRead(BaseModel):
    """Someone who owns an app or group: an app's owner can act as it, a group's
    owner decides its members. ``via``: service_principal | registration | group."""

    owner_id: str
    owner_name: str | None
    owner_type: str | None
    via: list[str]


class OwnedRead(BaseModel):
    """An app or group the identity owns, and the most it can do."""

    object_id: str
    object_name: str | None
    object_kind: str  # app | group
    via: list[str]
    level: str  # the object's strongest level among its current grants
    scope: str
    scope_name: str | None
    grants: int


class SignInRead(BaseModel):
    """When a person last signed in (newest of interactive, non-interactive and
    successful), as the newest check read it. Shown to admins and the person."""

    last_at: datetime | None  # None: no sign-in recorded
    read_at: datetime | None
    idle: bool  # past the idle threshold (Settings › Advice)
    idle_days: int


class CredentialRead(BaseModel):
    """A secret or certificate on an app: kind and dates only."""

    via: str  # registration | service_principal
    kind: str  # secret | certificate
    start_at: datetime | None
    end_at: datetime | None
    expired: bool
    long_lived: bool  # valid more than two years


class EntraActivation(BaseModel):
    """One PIM activation of an Entra role, from the Entra ID audit log: use of an
    eligibility, not a change of who has access."""

    at: datetime
    role_id: str
    role_name: str | None


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
    # Entra roles it activated through PIM, newest first (Entra ID audit log, when collected).
    entra_activations: list[EntraActivation] = []
    # Apps and groups: who owns it (null: no check could read them); registered
    # in another tenant: its owners are there.
    owners: list[OwnerRead] | None = None
    owners_elsewhere: bool = False
    # The apps and groups holding access that it owns.
    owns: list[OwnedRead] = []
    # A person: last sign-in (null: unknown, or not shown to this viewer).
    sign_in: SignInRead | None = None
    # An app: its secrets and certificates (null: no check read them).
    credentials: list[CredentialRead] | None = None


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
