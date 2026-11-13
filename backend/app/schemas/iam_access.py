from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScanStatus = Literal["pending", "running", "done", "failed"]
ScopeLevel = Literal["mgmt_group", "subscription", "resource_group", "resource"]


class ScanCreate(BaseModel):
    """Start a new scan.

    ``scope`` accepts any of:
      • full ARM path: ``/providers/.../managementGroups/foo``,
        ``/subscriptions/<guid>``, ``/subscriptions/<guid>/resourceGroups/<rg>``
      • bare mgmt-group name: ``foo`` (treated as mgmt group)
      • bare subscription GUID: ``8a3f-…``
    """

    scope: str = Field(min_length=1, max_length=2000)


class ScanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: str
    # Friendly name of the scan's root scope. Populated for subscriptions
    # (where ``scope`` is a GUID), MGs that have a separate display name,
    # and resource groups (last URL segment). Null while the scan is
    # ``pending`` — discovery hasn't run yet, so we don't know it.
    scope_name: str | None = None
    scope_level: ScopeLevel
    status: ScanStatus
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime
    role_assignment_count: int = 0
    unresolved_principals_count: int = 0
    # Places the identity was refused on (403): their own grants are unknown
    # to this check, and the last check that read them stays current there.
    unread_scopes: list[str] = []
    progress_total: int | None = None
    progress_done: int = 0
    progress_message: str | None = None


class RoleAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    principal_id: str
    principal_name: str | None
    principal_type: str
    role_name: str | None
    role_definition_id: str
    role_type: str | None = None
    # Server-side risk classification derived from the cached role
    # definition's actions list (``"high"`` or null). Combined with
    # ``scope_level`` by the frontend to decide whether to draw the
    # "R" chip — keeps the rule "Owner at MG = scary, Owner at RG =
    # noisy but expected" without hard-coding role names in the SPA.
    # Null when the role definition isn't cached yet (older scans).
    role_risk: str | None = None


class TreeNode(BaseModel):
    """A node in the scope hierarchy: mgmt group, subscription, RG, or resource."""

    scope: str
    scope_level: ScopeLevel
    name: str
    # Azure resource type (e.g. ``Microsoft.Storage/storageAccounts``)
    # for ``resource``-level nodes captured during discovery. Always
    # null for mgmt-group / subscription / resource-group nodes, and
    # for resource nodes synthesised by the API when only an RA scope
    # path was seen (no discovery walk reached them).
    resource_type: str | None = None
    role_assignments: list[RoleAssignmentRead] = []
    children: list["TreeNode"] = []


TreeNode.model_rebuild()


class ScanDiff(BaseModel):
    """Compare two scans of the same scope.

    An assignment's "identity" for diff purposes is the tuple
    ``(scope, role_definition_id, principal_id)`` — that way an Azure
    re-creation (which gets a new role_assignment id) is still detected
    as ``kept`` rather than ``removed`` + ``added``.
    """

    old_scan: ScanRead
    new_scan: ScanRead
    added: list[RoleAssignmentRead]
    removed: list[RoleAssignmentRead]
    kept_count: int


class RolePermission(BaseModel):
    """One Azure ``Permission`` block from a role definition.

    Built-in roles usually have a single permission block; custom roles
    can have multiple. Empty arrays for unused sub-fields keep the JSON
    shape stable for the UI.
    """

    actions: list[str] = []
    notActions: list[str] = []
    dataActions: list[str] = []
    notDataActions: list[str] = []


class GroupMemberRead(BaseModel):
    """One transitive member of a group, as persisted during the scan.

    Sourced from ``group_memberships`` rows for a given (scan, group)
    pair. Each member can be a User, Group (nested), ServicePrincipal,
    or Device — Graph flattens the chain for us via
    ``transitiveMembers``.
    """

    model_config = ConfigDict(from_attributes=True)

    member_id: str
    member_type: str | None
    member_name: str | None


class GroupMembersResult(BaseModel):
    """Response from ``GET /scans/{id}/groups/{group_id}/members``."""

    scan_id: int
    group_id: str
    members: list[GroupMemberRead]


class CoveredByRef(BaseModel):
    """An ancestor RA whose role is a superset of this RA's role.

    Surfaced on the Coverage tab so reviewers can see redundant
    grants: if alice has Owner @ mg-platform and Reader @ /sub/x,
    the Reader RA is "covered by" the Owner ancestor — the role is
    redundant.
    """

    assignment_id: int
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    role_name: str | None


class RoleRecommendation(BaseModel):
    """One narrower built-in role that would cover the observed activity.

    ``patterns_saved`` is current's granted_pattern_count minus this
    candidate's — useful as a sort key for "which suggestion is the
    biggest reduction".
    """

    role_definition_id: str
    role_name: str | None
    role_type: str | None
    granted_pattern_count: int
    patterns_saved: int


class TopOperation(BaseModel):
    """One observed operation + how often it fired in the window.

    Sorted by ``count`` descending. For right-sized rows where the
    role wildcards (Owner / Contributor / etc.) the "100% covered"
    headline can be misleading — seeing the actual top operations is
    what tells a reviewer if the role is justified or doing too much
    silently. For over-broad rows it pairs with ``unused_patterns``
    to give the full "what they did / what they didn't" picture.
    """

    operation_name: str
    count: int


class RoleCoverageRow(BaseModel):
    """Pattern-level least-privilege snapshot for one role assignment.

    Computed at request time by joining ``role_definitions`` (for the
    granted action patterns) with ``activity_events`` (for what the
    principal actually did in the window). See
    ``backend/app/core/role_coverage.py`` for the rules.
    """

    assignment_id: int
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
    granted_pattern_count: int
    used_pattern_count: int
    coverage_pct: int
    has_wildcard: bool
    unused_patterns: list[str]
    # Number of distinct ``operation_name`` strings observed for this
    # (principal, scope-prefix, window). Zero means we have events
    # for the principal but none under this RA's scope, OR no events
    # at all for the window. The UI uses this to distinguish "we
    # have data but nothing matched" from "no data".
    observed_op_count: int
    # Up to ~3 built-in roles whose actions cover what the principal
    # actually did, with fewer granted patterns than the current
    # role. Empty when ``observed_op_count == 0`` (no informed
    # recommendation possible) or when no built-in is tighter.
    recommendations: list[RoleRecommendation] = []
    # Up to 10 most-frequent ARM operations the principal performed
    # under this scope in the window, sorted by count desc. Empty
    # when ``observed_op_count == 0``. The UI shows them in the
    # details expander so reviewers can see what activity drove the
    # coverage figure — particularly important on right-sized
    # wildcard roles where 100% can hide breadth.
    top_operations: list[TopOperation] = []
    # Number of transitive members rolled into this row's coverage.
    # Zero for non-Group principals. For Group principals it's the
    # count of distinct user/SP/nested-group members we resolved via
    # Graph — activity from any of them counts as activity for this
    # RA's coverage. Null when we couldn't resolve the membership
    # (e.g. missing Graph permission); the UI then surfaces a
    # warning so 0% coverage isn't read as "unused".
    member_count: int | None = None
    # Ancestor RAs (same principal_id, broader-or-equal role) that
    # subsume this RA. Empty when nothing covers this row; non-empty
    # means this assignment is redundant — the principal already has
    # the access via a higher-scope grant.
    covered_by: list[CoveredByRef] = []


class UnattributedCaller(BaseModel):
    """One caller whose events we loaded but couldn't tie to any RA
    in this scan.

    Two practical reasons this happens:

    - The caller is an SP / managed identity whose role assignment
      lives at a scope we didn't scan (e.g. an MG-level grant) — its
      activity in this sub is real, but the RA is invisible to the
      coverage analysis.
    - The caller is an Azure system principal (``Microsoft.Advisor``,
      etc.) that doesn't surface as a standard RBAC assignment.

    To distinguish the two we look the caller up across *all* scans
    in our DB and report whether we have any record of how it got
    access: direct RAs anywhere, or group memberships anywhere. Zero
    on both counts → this caller is a true unknown, the more
    interesting finding.
    """

    caller: str
    caller_type: str | None
    event_count: int
    # Best-effort friendly name. Activity Log records SPs / managed
    # identities by GUID; users get a UPN. We try to resolve GUIDs
    # against ``role_assignments.principal_name`` and
    # ``group_memberships.member_name`` rows from OTHER scans —
    # free, no Graph call. Null when the caller has no record
    # anywhere (consistent with the "no record" case the UI
    # surfaces in red).
    caller_name: str | None = None
    # RAs in any scan whose ``principal_id`` matches this caller
    # (case-insensitive). Lookup is cross-scan — the caller might be
    # invisible in this scan but have a direct RA at MG level
    # captured by an earlier scan we still have.
    direct_assignment_count: int = 0
    # Distinct (scan, group) memberships in any scan where this
    # caller is a transitive member. Each is a path to indirect
    # access; the actual group's RA can be fetched via the
    # Principal access page.
    indirect_membership_count: int = 0


class RoleCoverageResult(BaseModel):
    """Top-level response from ``GET /scans/{id}/coverage``."""

    scan_id: int
    window_start: datetime
    window_end: datetime
    # How many events we considered across the relevant subscriptions.
    # Helps a reviewer judge confidence: 8 events over 90 days =
    # narrow window or sleepy sub; 80k events = lots of signal.
    events_considered: int
    # Of ``events_considered``, how many we successfully mapped to a
    # principal in this scan (either a direct RA principal or one of
    # its group members). ``events_unattributed`` = considered − attributed.
    # Both surfaced so reviewers see the breakdown without doing math.
    events_attributed: int = 0
    events_unattributed: int = 0
    # Subscriptions in the scan that had no events at all in our
    # cache for the window. Surfaced so the UI can prompt "run a
    # pull on this sub first".
    subscriptions_without_events: list[str]
    # Top callers whose events we loaded but couldn't tie to any RA
    # in this scan. Sorted by event count desc, capped at 20.
    unattributed_callers: list[UnattributedCaller] = []
    # Newest attributed event per principal id (lowercased) in the
    # window. Feeds ``principals.last_activity_at``.
    principal_last_activity: dict[str, datetime] = {}
    rows: list[RoleCoverageRow]


class RoleDefinitionRead(BaseModel):
    """Cached Azure role definition served to the UI's role-detail card."""

    model_config = ConfigDict(from_attributes=True)

    role_definition_id: str
    role_name: str | None
    role_type: str | None
    description: str | None
    permissions: list[RolePermission] | None
    assignable_scopes: list[str] | None
    fetched_at: datetime
    # Same classifier as ``RoleAssignmentRead.role_risk`` — exposed
    # here too so the role-detail drawer can render a "high-power"
    # banner without re-deriving from ``permissions`` on the client.
    role_risk: str | None = None
    # Short list of "why this role is high-risk" reasons, one per
    # distinct matching action pattern. Empty when ``role_risk`` is
    # null. Reviewer-friendly strings, not raw action ids.
    risk_reasons: list[str] = []


class RoleDefinitionListItem(BaseModel):
    """Row in the master list on the Roles page.

    Trimmed shape vs ``RoleDefinitionRead`` — drops ``permissions`` and
    ``assignable_scopes`` since the list view doesn't render them, plus
    adds a usage counter the SPA uses to highlight busy roles.
    """

    model_config = ConfigDict(from_attributes=True)

    role_definition_id: str
    role_name: str | None
    role_type: str | None
    description: str | None
    role_risk: str | None = None
    # Number of role-assignment rows across *all* scans referencing
    # this role. Includes inheritance (each persisted RA counts once).
    # Zero means the role has been observed during a scan walk but is
    # not actually granted to anyone today — a cleanup candidate for
    # custom roles.
    assignment_count: int = 0


class RoleUsageAssignment(BaseModel):
    """One RA + the scan it came from, served by the usage endpoint."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: int
    scan_id: int
    scan_scope: str
    scan_scope_level: ScopeLevel
    scan_finished_at: datetime | None
    scope: str
    scope_name: str | None
    scope_level: ScopeLevel
    principal_id: str
    principal_name: str | None
    principal_type: str


class RoleUsage(BaseModel):
    """Aggregate of where a role is used across every scan we have."""

    role_definition_id: str
    role_name: str | None
    # Totals are across all returned assignments (which are already
    # latest-scan-per-(principal,scope) — see endpoint docstring).
    assignment_count: int
    principal_count: int
    scope_count: int
    assignments: list[RoleUsageAssignment]
