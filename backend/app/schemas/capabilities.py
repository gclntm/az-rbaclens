"""Payload of ``GET /scopes/capabilities`` — the "Who can do what" tab."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.iam_access import ScopeLevel
from app.schemas.registry import ConditionFields, GrantOrigin

CapabilityLevel = Literal["access", "keys", "change", "data", "view", "unknown"]
IdentityKind = Literal["person", "group", "app", "other"]


class CapabilityGrant(ConditionFields, GrantOrigin):
    """One role assignment (or PIM eligibility) behind an identity's access here."""

    # Null for a PIM eligibility (it is not a role assignment).
    assignment_id: int | None
    role_name: str | None
    role_definition_id: str
    level: CapabilityLevel
    # ``here`` = assigned at this scope; ``inherited`` = assigned on a parent.
    how: Literal["here", "inherited"]
    from_scope_id: str
    from_scope_name: str | None
    from_scope_level: ScopeLevel
    # PIM: ``eligible`` = must be activated first; ``activated`` = an eligible
    # role switched on now. ``until`` = end date; null = permanent / no end.
    eligible: bool = False
    activated: bool = False
    until: datetime | None = None


class CapabilityIdentity(BaseModel):
    id: str
    name: str | None
    principal_type: str
    kind: IdentityKind
    # Most powerful level across ``grants``.
    level: CapabilityLevel
    # Any grant reaches data inside resources; ``data_write`` if it can change it.
    data: bool
    data_write: bool
    # What it can fetch, in words (levels access / keys).
    keys_for: list[str]
    # Active grants, strongest first.
    grants: list[CapabilityGrant]
    # PIM eligibilities, strongest first.
    eligible_grants: list[CapabilityGrant] = []
    # The identity's ``level`` is reachable only by activating an eligibility.
    on_request: bool = False
    # Groups: transitive members from the newest snapshot that read the
    # group; null when that membership was never resolved.
    member_count: int | None = None
    # Newest Activity Log event attributed to it (people and apps).
    last_activity_at: datetime | None = None
    # Apps and groups: how many people or apps own it (an app's owner can act as
    # it, a group's owner decides its members); null when no check could read them.
    owner_count: int | None = None
    # Apps registered in another tenant: their owners are there.
    owners_elsewhere: bool = False


class CapabilityLevelGroup(BaseModel):
    level: CapabilityLevel
    identities: list[CapabilityIdentity]
    people: int
    groups: int
    apps: int
    # Of ``identities``, how many are here only on request (PIM).
    on_request: int = 0


class InsideScope(BaseModel):
    """A child scope that grants access beyond what applies here."""

    scope_id: str
    name: str | None
    level: ScopeLevel
    # Identities granted there that have no access here, or a stronger level there.
    extra_identities: int
    top_level: CapabilityLevel
    by_level: dict[str, int]


class SnapshotRef(BaseModel):
    id: int
    scope_id: str
    name: str | None


class PimSummary(BaseModel):
    """What PIM says about this scope, across the snapshots that own it."""

    # ``read`` all owning snapshots listed PIM; ``partial`` some scopes
    # failed; ``failed`` none could; ``not_checked`` a snapshot predates PIM
    # support (take a new one).
    status: Literal["read", "partial", "failed", "not_checked"]
    error: str | None = None
    # Snapshots behind this scope's access that never read PIM (taken before
    # PIM support) — refreshing these completes the picture.
    unchecked: list[SnapshotRef] = []
    eligible_identities: int = 0
    time_bound_grants: int = 0
    # Identities at access / keys level through a grant with no end date.
    permanent_privileged: int = 0


class ScopeCapabilities(BaseModel):
    scope_id: str
    identities: int
    people: int
    groups: int
    apps: int
    # Distinct people who are members of the groups above (resolved groups only).
    people_via_groups: int
    groups_unresolved: int
    pim: PimSummary
    levels: list[CapabilityLevelGroup]
    inside: list[InsideScope]
    inside_total: int
