from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.findings import CurrentFindingRead, FindingsSummary
from app.schemas.iam_access import ScopeLevel
from app.tools.graph_resolver import is_microsoft_app


class LatestScanRef(BaseModel):
    id: int
    finished_at: datetime | None
    # Whole days since ``finished_at``; null when unknown.
    age_days: int | None


class ScopeStats(BaseModel):
    """Counts from the scope's latest completed scan."""

    # RAs whose scope is exactly this node.
    direct_assignments: int = 0
    # RAs at this node or any descendant (by path prefix).
    assignments_below: int = 0
    high_risk: int = 0
    open_findings: int = 0
    # Grants given on this node or inside it, by capability level.
    levels_below: dict[str, int] = Field(default_factory=dict)


class ScopeNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None
    level: ScopeLevel
    parent_id: str | None
    resource_type: str | None
    latest_scan: LatestScanRef | None = None
    stats: ScopeStats = Field(default_factory=ScopeStats)
    children: list["ScopeNode"] = Field(default_factory=list)


ScopeNode.model_rebuild()


class ScopeCrumb(BaseModel):
    id: str
    name: str | None
    level: ScopeLevel


class ScopeDetail(BaseModel):
    scope: ScopeNode
    # Root first, ending with the parent of ``scope``.
    ancestors: list[ScopeCrumb]
    # Immediate children (resources included only when requested).
    children: list[ScopeNode]
    first_seen_at: datetime
    last_seen_at: datetime


class PrincipalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str | None
    principal_type: str | None
    resolved_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_activity_at: datetime | None
    # Direct RAs across the latest completed scan of every root.
    assignment_count: int = 0
    # Groups (in latest scans) this principal is a transitive member of.
    group_count: int = 0
    # Strongest capability level across its own current grants (None = no grant).
    level: str | None = None
    # Service principals: the app's publishing tenant and kind, from Graph.
    app_owner_org: str | None = None
    sp_type: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def microsoft(self) -> bool:
        """An app Microsoft publishes for one of its services (Defender, AKS…)."""
        return is_microsoft_app(self.app_owner_org)


class RegistryRebuildResult(BaseModel):
    scans: int
    scopes: int
    principals: int


# --------------------------------------------------------------------------------------
# Scope page
# --------------------------------------------------------------------------------------

Source = Literal["direct", "inherited", "below"]


class AssignmentFindingRef(BaseModel):
    finding_id: int
    fingerprint: str
    category: str
    severity: str
    status: str


class ConditionFields(BaseModel):
    """An ABAC condition narrowing the assignment, in plain words (core/conditions.py)."""

    condition_kind: Literal["delegation", "storage", "other"] | None = None
    condition_text: str | None = None


class GrantOrigin(BaseModel):
    """Since when the grant exists and who gave it, as Azure records it.

    For a role assignment: ``createdOn`` / ``createdBy``. For a PIM
    eligibility: its start; the PIM schedule does not say who made it.
    ``given_by_type`` is the creator's RBAC principal type
    (``ServicePrincipal`` is usually a pipeline). All null on snapshots
    taken before migration 0025 that kept no ``raw``.
    """

    # Stable grant id (``grant_fingerprint``) for ``/grants/{id}``; null for
    # a PIM eligibility, which is not a role assignment.
    grant_id: str | None = None
    since: datetime | None = None
    given_by_id: str | None = None
    given_by_name: str | None = None
    given_by_type: str | None = None


class ScopeAssignmentRow(ConditionFields, GrantOrigin):
    assignment_id: int
    scan_id: int
    source: Source
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    principal_id: str
    principal_name: str | None
    principal_type: str
    role_name: str | None
    role_definition_id: str
    role_type: str | None
    role_risk: str | None
    # From the cached coverage of the RA's snapshot; null when coverage
    # has not been computed for that snapshot.
    coverage_pct: int | None = None
    granted_pattern_count: int | None = None
    observed_op_count: int | None = None
    member_count: int | None = None
    covered_by_count: int = 0
    findings: list[AssignmentFindingRef] = Field(default_factory=list)
    # PIM: an eligible role switched on, and when this grant ends (null = permanent).
    activated: bool = False
    until: datetime | None = None


class ScopeEligibleRow(ConditionFields, GrantOrigin):
    """A PIM eligibility at, above or below the scope (not a role assignment)."""

    source: Source
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    principal_id: str
    principal_name: str | None
    principal_type: str
    role_name: str | None
    role_definition_id: str
    member_type: str | None
    # Null = eligible with no end date.
    until: datetime | None


class ScopeAssignments(BaseModel):
    scope_id: str
    counts: dict[str, int]
    rows: list[ScopeAssignmentRow]
    # PIM eligibilities, same sources; empty when none or PIM wasn't read.
    eligible: list[ScopeEligibleRow] = []


class ScopeFindingRead(CurrentFindingRead):
    source: Source


class ScopeFindings(BaseModel):
    scope_id: str
    summary: FindingsSummary
    findings: list[ScopeFindingRead]


class ScopeSnapshot(BaseModel):
    id: int
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    role_assignment_count: int
    # True when this snapshot currently owns the requested scope.
    is_current: bool
    # Previous completed snapshot of the same root, for a diff link.
    previous_done_id: int | None


class PullRef(BaseModel):
    id: int
    status: str
    start_time: datetime
    end_time: datetime
    finished_at: datetime | None
    events_persisted: int


class SubscriptionActivity(BaseModel):
    id: str
    name: str | None
    events_90d: int
    newest_event_at: datetime | None
    last_pull: PullRef | None
    last_successful_pull: PullRef | None


class ActiveAssignment(BaseModel):
    assignment_id: int
    principal_id: str
    principal_name: str | None
    principal_type: str
    role_name: str | None
    scope: str
    scope_name: str | None
    source: Source
    observed_op_count: int
    event_count: int
    top_operations: list[dict[str, Any]]


class ScopeActivity(BaseModel):
    scope_id: str
    subscriptions: list[SubscriptionActivity]
    # From the cached coverage of the snapshot that owns the scope; null
    # when the scope has no owning snapshot or coverage was never computed.
    snapshot_id: int | None
    computed_at: datetime | None
    window_days: int | None
    events_considered: int | None
    events_attributed: int | None
    events_unattributed: int | None
    unattributed_callers: list[dict[str, Any]]
    most_active: list[ActiveAssignment]
