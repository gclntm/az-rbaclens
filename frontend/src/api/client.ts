import { filenameFrom } from "./filename";

const BASE = "/api";

/**
 * Auth-token injection hook. ``AuthProvider`` registers a callback
 * that returns a fresh MSAL access token (or null when auth is off).
 * The default is a no-op so unauthenticated builds work unchanged.
 *
 * When ``forceRefresh`` is set the provider bypasses MSAL's in-memory
 * cache. We use that on retry-after-401 to handle the case where the
 * cached token has gone stale (server clock skew, very long tab life,
 * tenant-side revocation).
 */
type TokenProvider = (opts?: { forceRefresh?: boolean }) => Promise<string | null>;
let tokenProvider: TokenProvider = async () => null;

export function setApiTokenProvider(fn: TokenProvider): void {
  tokenProvider = fn;
}

async function send(
  path: string,
  init: RequestInit | undefined,
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(`${BASE}${path}`, { ...init, headers });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await authed(path, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** A plain-text endpoint (a script to read), with the same auth and errors. */
async function requestText(path: string): Promise<string> {
  return (await authed(path)).text();
}

async function authed(path: string, init?: RequestInit): Promise<Response> {
  const token = await tokenProvider();
  let response = await send(path, init, token);
  // If we had a token and the server still says 401, the token is
  // probably stale. Force-refresh once and retry; if that still fails,
  // surface the error to the caller (AuthProvider will redirect to
  // sign-in on the next interactive flow).
  if (response.status === 401 && token) {
    const fresh = await tokenProvider({ forceRefresh: true });
    if (fresh && fresh !== token) {
      response = await send(path, init, fresh);
    }
  }
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${response.statusText}${text ? ` — ${text}` : ""}`);
  }
  return response;
}

/**
 * Save a file from the API with the sign-in token. A plain ``<a href>`` can't
 * send it, so with sign-in on, every export link goes through here.
 */
export async function downloadFile(url: string): Promise<void> {
  const path = url.startsWith(BASE) ? url.slice(BASE.length) : url;
  const response = await authed(path);
  const blob = await response.blob();
  const name =
    filenameFrom(response.headers.get("Content-Disposition")) ?? path.split("?")[0].split("/").pop() ?? "download";
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 60_000);
}

export type HealthResponse = {
  status: "ok" | "degraded";
  /** Deployed version (backend/app/version.py). */
  version: string;
  stage: "test" | "prod";
  db: "ok" | "unreachable";
};

export type ScanStatus = "pending" | "running" | "done" | "failed";
/** "directory": the Entra ID place, above the Tenant Root Group (Entra roles). */
export type ScopeLevel = "directory" | "mgmt_group" | "subscription" | "resource_group" | "resource";

export type Scan = {
  id: number;
  scope: string;
  /**
   * Friendly name of the scan root, derived from `scan.hierarchy` at
   * serialization time. Null while the scan is still `pending` — fall
   * back to the trailing path segment of `scope` for display.
   */
  scope_name: string | null;
  scope_level: ScopeLevel;
  status: ScanStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
  role_assignment_count: number;
  unresolved_principals_count: number;
  /** Places the identity was refused on: their own grants are unknown to this check. */
  unread_scopes?: string[];
  progress_total: number | null;
  progress_done: number;
  progress_message: string | null;
};

export type RoleAssignment = {
  id: number;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  role_name: string | null;
  role_definition_id: string;
  role_type: string | null;
  /**
   * Server-side risk classification derived from the cached role
   * definition's actions list. ``"high"`` means the role grants
   * broad write / RBAC-mutating power. Null when the role definition
   * isn't cached (older scans) — the UI falls back to a name-based
   * heuristic in that case.
   */
  role_risk: string | null;
};

export type TreeNode = {
  scope: string;
  scope_level: ScopeLevel;
  name: string;
  /**
   * Azure resource type string (e.g. ``microsoft.storage/storageaccounts``)
   * for resource-level nodes that were enumerated during the scan.
   * Null for MG / sub / RG nodes and for resource scopes the API
   * synthesised from RA paths that weren't seen during discovery.
   */
  resource_type: string | null;
  role_assignments: RoleAssignment[];
  children: TreeNode[];
};

export type ScanDiff = {
  old_scan: Scan;
  new_scan: Scan;
  added: RoleAssignment[];
  removed: RoleAssignment[];
  kept_count: number;
};

export type GroupMember = {
  member_id: string;
  member_type: string | null;
  member_name: string | null;
};

export type GroupMembersResult = {
  scan_id: number;
  group_id: string;
  members: GroupMember[];
};

export type RolePermission = {
  actions: string[];
  notActions: string[];
  dataActions: string[];
  notDataActions: string[];
};

export type RoleDefinitionDetail = {
  role_definition_id: string;
  role_name: string | null;
  role_type: string | null;
  description: string | null;
  permissions: RolePermission[] | null;
  assignable_scopes: string[] | null;
  fetched_at: string;
  role_risk: string | null;
  risk_reasons: string[];
};

export type RoleDefinitionListItem = {
  role_definition_id: string;
  role_name: string | null;
  role_type: string | null;
  description: string | null;
  role_risk: string | null;
  assignment_count: number;
};

export type RoleListSort = "name" | "usage" | "risk";

export type RoleListFilters = {
  q?: string;
  role_type?: "BuiltInRole" | "CustomRole";
  risk?: "high";
  unused?: boolean;
  sort?: RoleListSort;
};

export type RoleUsageAssignment = {
  assignment_id: number;
  scan_id: number;
  scan_scope: string;
  scan_scope_level: ScopeLevel;
  scan_finished_at: string | null;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
};

export type RoleUsage = {
  role_definition_id: string;
  role_name: string | null;
  assignment_count: number;
  principal_count: number;
  scope_count: number;
  assignments: RoleUsageAssignment[];
};

export type SubscriptionNode = {
  id: string;
  display_name: string;
  scope: string;
  mgmt_group_id: string | null;
};

export type RoleAssignmentFilters = {
  principal_type?: string;
  role_name?: string;
  scope_level?: ScopeLevel;
};

export type CredentialSource = "env" | "db" | "default" | "none";

export type CredentialsRead = {
  tenant_id: string | null;
  client_id: string | null;
  client_secret_set: boolean;
  source: CredentialSource;
  updated_at: string | null;
  encryption_available: boolean;
};

export type CredentialsWrite = {
  tenant_id?: string | null;
  client_id?: string | null;
  client_secret?: string | null;
};

export type AuthConfigPublic = {
  enabled: boolean;
  tenant_id: string | null;
  client_id: string | null;
  authority: string | null;
};

export type AuthSettingsRead = {
  enabled: boolean;
  tenant_id: string | null;
  client_id: string | null;
  allowed_group_ids: string[];
  admin_group_ids: string[];
  updated_at: string | null;
};

export type AuthSettingsWrite = {
  enabled?: boolean;
  tenant_id?: string | null;
  client_id?: string | null;
  allowed_group_ids?: string[];
  admin_group_ids?: string[];
};

export type AuditEvent = {
  id: number;
  occurred_at: string;
  actor: string | null;
  actor_name: string | null;
  method: string;
  path: string;
  status_code: number;
  client_ip?: string | null;
  /** What the change changed, when the endpoint says so (Settings › Data kept: before / after). */
  detail?: { before?: Record<string, unknown>; after?: Record<string, unknown> } | null;
};

export type TableStats = {
  name: string;
  row_count: number | null;
  total_size_bytes: number | null;
  table_size_bytes: number | null;
  index_size_bytes: number | null;
};

export type IndexStats = {
  name: string;
  table_name: string;
  size_bytes: number | null;
  is_unique: boolean | null;
};

export type DatabaseStats = {
  dialect: string;
  database_size_bytes: number | null;
  tables: TableStats[];
  indexes: IndexStats[];
  fetched_at: string;
};

export type MaintenanceResult = {
  operation: string;
  duration_ms: number;
  detail: string;
  rows_affected: number | null;
};

export type ActivityPullStatus = "pending" | "running" | "done" | "failed";

export type ActivityPull = {
  id: number;
  subscription_id: string;
  start_time: string;
  end_time: string;
  status: ActivityPullStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  events_seen: number;
  events_persisted: number;
  progress_done: number;
  progress_message: string | null;
  created_at: string;
};

export type ActivityEvent = {
  id: number;
  subscription_id: string;
  event_data_id: string;
  caller: string | null;
  caller_type: string | null;
  operation_name: string;
  /** The operation in words (Azure's display name, or one built from the operation). */
  operation_display?: string | null;
  resource_id: string | null;
  resource_type: string | null;
  status: string | null;
  correlation_id: string | null;
  occurred_at: string;
  /** How many events the row stands for: more than 1 for a daily row of machine activity. */
  event_count?: number;
  /** Daily rows: the first of their events (``occurred_at`` is the last). */
  first_occurred_at?: string | null;
};

/** "×24 from 08:01" for a daily row of machine activity; empty for an event kept in full. */
export function dailyCountText(e: Pick<ActivityEvent, "event_count" | "first_occurred_at">): string {
  const n = e.event_count ?? 1;
  if (n <= 1 && !e.first_occurred_at) return "";
  const from = e.first_occurred_at
    ? ` from ${new Date(e.first_occurred_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
    : "";
  return `×${n.toLocaleString()}${from}`;
}

export type ActivityEventFilters = {
  subscription_id?: string;
  caller?: string;
  operation_name?: string;
  status?: string;
  from?: string;
  to?: string;
  limit?: number;
};

export type ActivityEventDetail = ActivityEvent & {
  raw: Record<string, unknown> | null;
};

function activityFilterParams(filters: ActivityEventFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (filters.subscription_id) sp.set("subscription_id", filters.subscription_id);
  if (filters.caller) sp.set("caller", filters.caller);
  if (filters.operation_name) sp.set("operation_name", filters.operation_name);
  if (filters.status) sp.set("status", filters.status);
  if (filters.from) sp.set("from", filters.from);
  if (filters.to) sp.set("to", filters.to);
  if (filters.limit) sp.set("limit", String(filters.limit));
  return sp;
}

// ---------------------------------------------------------------------------
// Registries, scope page, findings, overview (backend 0017–0019)
//
// Everything below reads the *current* RBAC picture: each RA comes from the
// snapshot that currently owns its scope, so nothing double counts across
// overlapping scans.
// ---------------------------------------------------------------------------

export type LatestScanRef = {
  id: number;
  finished_at: string | null;
  age_days: number | null;
};

export type ScopeStats = {
  direct_assignments: number;
  assignments_below: number;
  high_risk: number;
  open_findings: number;
  /** Grants given here and inside, by capability level. */
  levels_below?: Partial<Record<CapabilityLevel, number>>;
};

export type ScopeNode = {
  id: string;
  name: string | null;
  level: ScopeLevel;
  parent_id: string | null;
  resource_type: string | null;
  /** Null when no completed scan has walked this scope (e.g. Tenant Root Group). */
  latest_scan: LatestScanRef | null;
  stats: ScopeStats;
  children: ScopeNode[];
};

export type ScopeCrumb = { id: string; name: string | null; level: ScopeLevel };

export type ScopeDetail = {
  scope: ScopeNode;
  /** Root first, ending with the scope's parent. */
  ancestors: ScopeCrumb[];
  children: ScopeNode[];
  first_seen_at: string;
  last_seen_at: string;
};

export type AssignmentSource = "direct" | "inherited" | "below";
export type FindingSeverity = "high" | "medium" | "low";
export type FindingStatus = "open" | "reviewed" | "accepted_risk" | "snoozed" | "resolved";
export type DecisionKind = "reviewed" | "accepted_risk" | "snoozed" | "resolved";

export type AssignmentFindingRef = {
  finding_id: number;
  fingerprint: string;
  category: string;
  severity: FindingSeverity;
  status: FindingStatus;
};

export type ConditionKind = "delegation" | "storage" | "other";

/** Since when a grant exists and who gave it, as Azure records it (all null when unknown). */
export type GrantOrigin = {
  /** Stable id for /grants/:id; null for a PIM eligibility. */
  grant_id?: string | null;
  since?: string | null;
  given_by_id?: string | null;
  given_by_name?: string | null;
  /** User / Group / ServicePrincipal (usually a pipeline). */
  given_by_type?: string | null;
};

export type ScopeAssignmentRow = GrantOrigin & {
  /** PIM: must be activated first / an eligible role switched on / end date (null = no end). */
  eligible?: boolean;
  activated?: boolean;
  until?: string | null;
  /** ABAC condition narrowing the assignment, in plain words; null when none. */
  condition_kind?: ConditionKind | null;
  condition_text?: string | null;
  assignment_id: number;
  scan_id: number;
  source: AssignmentSource;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  role_name: string | null;
  role_definition_id: string;
  role_type: string | null;
  role_risk: string | null;
  /** Null when coverage was never computed for the RA's snapshot. */
  coverage_pct: number | null;
  granted_pattern_count: number | null;
  observed_op_count: number | null;
  member_count: number | null;
  covered_by_count: number;
  findings: AssignmentFindingRef[];
};

export type ScopeEligibleRow = GrantOrigin & {
  condition_kind?: ConditionKind | null;
  condition_text?: string | null;
  source: AssignmentSource;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  role_name: string | null;
  role_definition_id: string;
  member_type: string | null;
  until: string | null;
};

export type ScopeAssignments = {
  scope_id: string;
  counts: Record<AssignmentSource, number>;
  rows: ScopeAssignmentRow[];
  /** PIM eligibilities at, above or below the scope. */
  eligible: ScopeEligibleRow[];
};

export type ReviewDecision = {
  fingerprint: string;
  decision: DecisionKind;
  note: string | null;
  snoozed_until: string | null;
  actor: string | null;
  actor_name: string | null;
  decided_at: string;
};

export type Finding = {
  id: number;
  scan_id: number;
  role_assignment_id: number | null;
  fingerprint: string;
  category: string;
  severity: FindingSeverity;
  title: string;
  detail: string;
  suggestion: string | null;
  evidence: Record<string, unknown> | null;
  computed_at: string;
  status: FindingStatus;
  decision: ReviewDecision | null;
};

/** A finding on a current RA, with the context a cross-scope list needs. */
export type CurrentFinding = Finding & {
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  role_name: string | null;
};

export type ScopeFinding = CurrentFinding & { source: AssignmentSource };

export type CurrentFindings = {
  summary: FindingsSummary;
  /** Rows matching the filters, before paging. */
  total: number;
  offset: number;
  limit: number;
  findings: CurrentFinding[];
};

export type FindingListParams = {
  status?: FindingStatus | "decided";
  severity?: FindingSeverity;
  category?: string;
  q?: string;
  limit?: number;
  offset?: number;
  /** "grant": page by grant, each with all its matching findings; total, offset and limit count grants. */
  per?: "finding" | "grant";
};

/** Retention policy from server settings; 0 means keep everything. */
export type RetentionPolicy = {
  interval_hours: number;
  event_retention_days: number;
  snapshot_keep_per_scope: number;
  failed_snapshot_retention_days: number;
  audit_retention_days: number;
  change_retention_days?: number;
};

/** One line of Settings › Data kept: how much is stored now. */
export type DataKeptStored = { count: number; oldest: string | null; bytes: number | null };

/** Settings › Data kept: what applies (saved over the server's), and what is stored. */
export type DataKept = {
  values: Record<string, number>;
  sources: Record<string, "saved" | "server">;
  server: Record<string, number>;
  bounds: Record<string, [number, number]>;
  /** Values that also accept 0 below their bounds, meaning "never". */
  never: string[];
  stored: Record<string, DataKeptStored>;
  folded_until: string | null;
  bytes_per_check: number | null;
  checks_per_day: number;
  retention_interval_hours: number;
};

export type RetentionRun = {
  duration_ms: number;
  events_deleted: number;
  snapshots_deleted: number;
  failed_snapshots_deleted: number;
  audit_deleted: number;
  pulls_deleted?: number;
};

/** The Activity Log target that stands for the logs of all management groups (backend: core/activity_targets.py). */
/** Entra ID as a place, above the Tenant Root Group: who holds which Entra role (core/entra_roles.py). */
export const ENTRA_PLACE = "/providers/microsoft.aadiam";
export const isEntraPlace = (scope: string | null | undefined) => (scope ?? "").toLowerCase() === ENTRA_PLACE;
export const MGMT_GROUPS = "management-groups";
export const MGMT_GROUPS_NAME = "Management groups";
/** The Entra ID audit log: who adds or removes group members (core/activity_targets.py). */
export const ENTRA_AUDIT = "entra-directory";
export const ENTRA_AUDIT_NAME = "Entra ID audit log";

export type JobKind = "snapshot" | "activity_pull";

export type JobSchedule = {
  id: number;
  kind: JobKind;
  target: string;
  every_hours: number;
  /** Daily schedules: the hour each run is aligned to, container local time. */
  at_hour?: number | null;
  enabled: boolean;
  initial_window_days: number;
  next_run_at: string;
  last_run_at: string | null;
  last_job_id: number | null;
  last_job_status: ScanStatus | null;
  /** Why the last run failed (kept until one succeeds), or why it could not start. */
  last_error: string | null;
  /** The same in one plain sentence. */
  last_error_summary?: string | null;
  /** When it was switched off, and by whom (no name while sign-in is off). */
  paused_at?: string | null;
  paused_by?: string | null;
  /** Failed runs in a row; 0 when the last one worked. */
  failures?: number;
  /** Collections: the Activity Log is collected up to here by this schedule. */
  covered_until?: string | null;
  created_at: string;
};

export type JobScheduleCreate = {
  kind: JobKind;
  target: string;
  every_hours: number;
  at_hour?: number | null;
  enabled?: boolean;
  initial_window_days?: number;
  start_now?: boolean;
};

/** ``at_hour``: 0–23 for a daily schedule, -1 to clear it (a plain interval). */
export type JobScheduleUpdate = Partial<
  Pick<JobSchedule, "enabled" | "every_hours" | "initial_window_days">
> & { at_hour?: number };

/** Which recent runs to list; both filter before the limit. */
export type JobFilter = { kind?: JobKind; status?: ScanStatus };

/** A scan or an activity pull, merged for the Jobs page. */
export type Job = {
  kind: JobKind;
  id: number;
  target: string;
  status: ScanStatus;
  progress_message: string | null;
  error: string | null;
  /** The error in one plain sentence. */
  error_summary?: string | null;
  /** What started it; null for runs from before this was recorded. */
  origin?: "schedule" | "manual" | "recheck" | null;
  schedule_id?: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** Checks: places the identity was refused on (their grants are unknown). */
  unread?: number;
  window_start: string | null;
  window_end: string | null;
  events_persisted: number | null;
};

/** "Who can do what": five plain levels, most powerful first; ``unknown`` = role never cached. */
export type CapabilityLevel = "access" | "keys" | "change" | "data" | "view" | "unknown";
export type IdentityKind = "person" | "group" | "app" | "other";

export type CapabilityGrant = GrantOrigin & {
  /** PIM: must be activated first / an eligible role switched on / end date (null = no end). */
  eligible?: boolean;
  activated?: boolean;
  until?: string | null;
  /** ABAC condition narrowing the assignment, in plain words; null when none. */
  condition_kind?: ConditionKind | null;
  condition_text?: string | null;
  assignment_id: number | null;
  role_name: string | null;
  role_definition_id: string;
  level: CapabilityLevel;
  how: "here" | "inherited";
  /** Null for a PIM eligibility. */
  from_scope_id: string;
  from_scope_name: string | null;
  from_scope_level: ScopeLevel;
};

export type CapabilityIdentity = {
  id: string;
  name: string | null;
  principal_type: string;
  kind: IdentityKind;
  level: CapabilityLevel;
  data: boolean;
  data_write: boolean;
  keys_for: string[];
  /** Active grants, strongest first. */
  grants: CapabilityGrant[];
  /** PIM eligibilities, strongest first. */
  eligible_grants: CapabilityGrant[];
  /** The level is reachable only by activating an eligibility. */
  on_request: boolean;
  member_count: number | null;
  last_activity_at: string | null;
  /** Apps and groups: how many own it (an app's owner can act as it, a group's owner decides its members); null = unknown. */
  owner_count?: number | null;
  /** An app registered in another tenant: its owners are there. */
  owners_elsewhere?: boolean;
};

/** Someone who owns an app or group; ``via``: service_principal | registration | group. */
export type OwnerRef = { owner_id: string; owner_name: string | null; owner_type: string | null; via: string[] };

/** An app or group the identity owns, and the most it can do. */
export type OwnedRef = {
  object_id: string;
  object_name: string | null;
  object_kind: "app" | "group";
  via: string[];
  level: CapabilityLevel;
  scope: string;
  scope_name: string | null;
  grants: number;
};

export type CapabilityLevelGroup = {
  level: CapabilityLevel;
  identities: CapabilityIdentity[];
  people: number;
  groups: number;
  apps: number;
  on_request: number;
};

export type InsideScope = {
  scope_id: string;
  name: string | null;
  level: ScopeLevel;
  extra_identities: number;
  top_level: CapabilityLevel;
  by_level: Partial<Record<CapabilityLevel, number>>;
};

export type PimSummary = {
  status: "read" | "partial" | "failed" | "not_checked";
  error: string | null;
  /** Snapshots behind this scope's access that never read PIM. */
  unchecked: { id: number; scope_id: string; name: string | null }[];
  eligible_identities: number;
  time_bound_grants: number;
  permanent_privileged: number;
};

export type ScopeCapabilities = {
  scope_id: string;
  identities: number;
  people: number;
  groups: number;
  apps: number;
  people_via_groups: number;
  groups_unresolved: number;
  pim: PimSummary;
  levels: CapabilityLevelGroup[];
  inside: InsideScope[];
  inside_total: number;
};

export type PullRef = {
  id: number;
  status: ScanStatus;
  start_time: string;
  end_time: string;
  finished_at: string | null;
  events_persisted: number;
};

export type ScopeActivity = {
  scope_id: string;
  subscriptions: {
    id: string;
    name: string | null;
    events_90d: number;
    newest_event_at: string | null;
    last_pull: PullRef | null;
    last_successful_pull: PullRef | null;
  }[];
  snapshot_id: number | null;
  computed_at: string | null;
  window_days: number | null;
  events_considered: number | null;
  events_attributed: number | null;
  events_unattributed: number | null;
  unattributed_callers: {
    caller: string;
    caller_type: string | null;
    event_count: number;
    caller_name: string | null;
    direct_assignment_count: number;
    indirect_membership_count: number;
  }[];
  most_active: {
    assignment_id: number;
    principal_id: string;
    principal_name: string | null;
    principal_type: string;
    role_name: string | null;
    scope: string;
    scope_name: string | null;
    source: AssignmentSource;
    observed_op_count: number;
    event_count: number;
    top_operations: { operation_name: string; display?: string | null; count: number }[];
  }[];
};

export type FindingsSummary = {
  total: number;
  open: number;
  by_severity: Partial<Record<FindingSeverity, number>>;
  by_category: Record<string, number>;
  by_status: Partial<Record<FindingStatus, number>>;
  /** The grants these findings are on, and those with one open (the Needs a look list only). */
  grants?: number | null;
  open_grants?: number | null;
};

export type ScopeFindings = {
  scope_id: string;
  summary: FindingsSummary;
  findings: ScopeFinding[];
};

export type ScopeSnapshot = {
  id: number;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  status: ScanStatus;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  role_assignment_count: number;
  is_current: boolean;
  previous_done_id: number | null;
};

export type DecisionWrite = {
  decision: DecisionKind;
  note?: string | null;
  snoozed_until?: string | null;
};

export type RegistryPrincipal = {
  id: string;
  display_name: string | null;
  principal_type: string | null;
  resolved_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  last_activity_at: string | null;
  assignment_count: number;
  group_count: number;
  /** Strongest capability level across its own current grants; null = none. */
  level: CapabilityLevel | null;
  /** Service principals, from Graph: the app's publishing tenant, its kind, and whether Microsoft publishes it. */
  app_owner_org?: string | null;
  sp_type?: string | null;
  microsoft?: boolean;
};

export type PrincipalAccessRow = GrantOrigin & {
  /** PIM: must be activated first / an eligible role switched on / end date (null = no end). */
  eligible?: boolean;
  activated?: boolean;
  until?: string | null;
  /** ABAC condition narrowing the assignment, in plain words; null when none. */
  condition_kind?: ConditionKind | null;
  condition_text?: string | null;
  assignment_id: number | null;
  scan_id: number;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  subscription_id: string | null;
  subscription_name: string | null;
  role_name: string | null;
  role_definition_id: string;
  role_type: string | null;
  role_risk: string | null;
  /** What the role lets the holder do. */
  level: CapabilityLevel;
  is_high_risk: boolean;
  source: "direct" | "group";
  via_group_id: string | null;
  via_group_name: string | null;
  /** Coverage of the RA; for a group-held RA it is the whole group's activity. */
  observed_op_count: number | null;
  coverage_pct: number | null;
  open_findings: number;
  /** The playbook's verdict when there is something to do (null for PIM eligibilities). */
  advice_weight?: AdviceWeight | null;
  advice_title?: string | null;
};

export type PrincipalGroupLink = {
  group_id: string;
  group_name: string | null;
  scan_id: number;
  assignment_count: number;
  high_risk_count: number;
};

export type PrincipalMember = {
  member_id: string;
  member_name: string | null;
  member_type: string | null;
};

export type PrincipalPage = {
  principal: RegistryPrincipal;
  summary: {
    direct: number;
    via_group: number;
    high_risk: number;
    open_findings: number;
    subscriptions: number;
    eligible: number;
  };
  assignments: PrincipalAccessRow[];
  /** Roles the principal can activate through PIM. */
  eligible: PrincipalAccessRow[];
  groups: PrincipalGroupLink[];
  /** Groups only; null for users and service principals. */
  members: PrincipalMember[] | null;
  members_total: number;
  /** Groups: a check read the members from Graph (none + read = the group is empty). */
  members_read?: boolean;
  findings: CurrentFinding[];
  /** "What to do": this identity's own grants, one line per playbook rule. */
  advice?: IdentityAdvice | null;
  /** Entra roles it activated through PIM, newest first (from the Entra ID audit log). */
  entra_activations?: { at: string; role_id: string; role_name: string | null }[];
  /** Apps and groups: who owns it (null = unknown); registered in another tenant. */
  owners?: OwnerRef[] | null;
  owners_elsewhere?: boolean;
  /** The apps and groups holding access that it owns. */
  owns?: OwnedRef[];
};

export type PrincipalActivity = {
  principal_id: string;
  window_days: number;
  identities: number;
  event_count: number;
  operation_count: number;
  newest_at: string | null;
  callers: string[];
  by_subscription: {
    subscription_id: string;
    subscription_name: string | null;
    count: number;
    newest_at: string | null;
  }[];
  top_operations: { operation: string; display?: string | null; count: number; newest_at: string | null }[];
  failed_count: number;
  recent: ActivityEvent[];
};

export type SearchHit = { id: string; name: string | null; kind: string | null };

export type SearchResults = {
  query: string;
  scopes: SearchHit[];
  principals: SearchHit[];
  roles: SearchHit[];
};

export type OverviewKpis = {
  assignments: number;
  principals: number;
  high_risk_broad: number;
  unused: number;
  unresolved_principals: number;
  open_findings: number;
  added_30d: number;
  removed_30d: number;
  /** Open findings except "unused" (evidence, not a problem). */
  needs_look: number;
  /** Identities by the strongest thing any of their grants lets them do, anywhere. */
  by_level: Record<CapabilityLevel, number>;
};

export type AttentionItem = {
  finding_id: number;
  fingerprint: string;
  scan_id: number;
  category: string;
  severity: FindingSeverity;
  title: string;
  detail: string;
  suggestion: string | null;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name?: string | null;
  role_name?: string | null;
  grant_id?: string | null;
};

export type OverviewJob = {
  kind: "snapshot" | "activity_pull";
  id: number;
  target: string;
  target_name: string | null;
  status: ScanStatus;
  progress_done: number;
  progress_total: number | null;
  progress_message: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type Overview = {
  kpis: OverviewKpis;
  /** When the newest daily check behind the current picture finished. */
  access_as_of: string | null;
  /** Newest Activity Log event collected. */
  activity_up_to: string | null;
  attention: AttentionItem[];
  top_roles: {
    role_name: string | null;
    role_definition_id: string;
    role_risk: string | null;
    count: number;
  }[];
  freshness: {
    window_days: number;
    subscriptions_total: number;
    subscriptions_fresh: number;
    stale: { id: string; name: string | null; level: ScopeLevel; age_days: number | null }[];
  };
  jobs: OverviewJob[];
  /** Contributing snapshots whose findings were never computed. */
  findings_pending: number[];
};

export type ChangeKind =
  | "granted"
  | "removed"
  | "eligible_added"
  | "eligible_removed"
  | "member_added"
  | "member_removed"
  | "owner_added"
  | "owner_removed";

export type AccessChange = {
  id: number;
  occurred_at: string;
  kind: ChangeKind;
  /**
   * activity_log: exact, with author · created_on: Azure's own record · entra_audit: the
   * Entra ID audit log (someone joining or leaving a group) · daily_check: first seen.
   */
  source: "activity_log" | "created_on" | "daily_check" | "entra_audit";
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string | null;
  role_definition_id: string | null;
  role_name: string | null;
  level: CapabilityLevel;
  via_group_id: string | null;
  via_group_name: string | null;
  actor_id: string | null;
  actor_name: string | null;
  /** "app" when Entra records an app as the author (Teams, access packages, PIM, AD sync); "user" for a person. */
  actor_type: string | null;
  /** The nested group the member was added to or removed from, inside via_group. */
  direct_group_name: string | null;
  grant_id: string | null;
  check_id: number | null;
};

export type ChangesResponse = {
  since: string;
  until: string;
  total: number;
  given: number;
  removed: number;
  powerful: number;
  weeks: { week_start: string; given: number; removed: number }[];
  items: AccessChange[];
};

export type ChangesParams = {
  scope?: string;
  principal?: string;
  days?: number;
  direction?: "all" | "given" | "removed";
  powerful_only?: boolean;
  limit?: number;
  offset?: number;
};

export type Freshness = {
  access_as_of: string | null;
  activity_up_to: string | null;
  daily_check_at_hour: number | null;
  /** The time zone that hour is in, e.g. "Europe/Zurich". */
  timezone?: string;
  next_check_at: string | null;
  last_check: { id: number; status: ScanStatus; finished_at: string | null; error: string | null } | null;
  /** What keeps the data from being kept up to date; empty = all fine. */
  warnings?: StaleWarning[];
};

export type StaleWarning = {
  kind:
    | "schedules_off"
    | "no_check"
    | "check_failed"
    | "check_stale"
    | "activity_behind"
    | "activity_at_risk"
    | "activity_lost";
  text: string;
  names: string[];
  path: string;
  /** "danger" when data is being lost or about to be. */
  severity?: "warn" | "danger";
};

export type CoverageState = "ok" | "behind" | "at_risk" | "lost" | "never" | "no_schedule";

/** How far one subscription's Activity Log is collected. */
export type SubscriptionCoverage = {
  subscription_id: string;
  name: string;
  schedule_id: number | null;
  enabled: boolean;
  collecting: boolean;
  covered_until: string | null;
  behind_hours: number | null;
  /** Days before the first uncollected event leaves Azure's 90 days; negative = already lost. */
  days_left: number | null;
  state: CoverageState;
  second_pass: { status: ScanStatus; finished_at: string | null; late_events: number } | null;
};

export type Coverage = {
  azure_keeps_days: number;
  at_risk_days_left: number;
  subscriptions: SubscriptionCoverage[];
  /** The nightly second pass that re-reads the last hours for late events; 0 hours = off. */
  second_pass_hours: number;
  second_pass_finished_at: string | null;
  second_pass_subscriptions: number;
  second_pass_late_events: number;
  second_pass_failed: number;
  entra_audit: EntraAudit;
};

export type EntraAuditStatus = "readable" | "no_permission" | "licence_required" | "unavailable";

/** Whether the Entra ID audit log can be read (who adds or removes group members), and its collection. */
export type EntraAudit = {
  /** null until it has been tried (after a check, or with Check now). */
  status: EntraAuditStatus | null;
  checked_at: string | null;
  message: string | null;
  schedule_id: number | null;
  enabled: boolean;
  collecting: boolean;
  covered_until: string | null;
};

export type ResponsibleRead = {
  person_id: string;
  person_name: string | null;
  person_email: string | null;
  source: "manual" | "tag";
  set_by_name: string | null;
  set_at: string;
};

export type ResponsibleNode = {
  scope_id: string;
  name: string | null;
  level: ScopeLevel;
  parent_id: string | null;
  depth: number;
  responsible: ResponsibleRead | null;
  /** The place the setting is on; equals scope_id when set here. */
  from_scope_id: string | null;
  from_scope_name: string | null;
};

export type ResponsibleOf = {
  scope_id: string;
  responsible: ResponsibleRead | null;
  from_scope_id: string | null;
  from_scope_name: string | null;
};

export type ResponsibleSuggestion = {
  scope_id: string;
  scope_name: string | null;
  level: ScopeLevel;
  tag_value: string;
  person_id: string | null;
  person_name: string | null;
  person_email: string | null;
  status: "open" | "unmatched" | "ignored" | "used";
  current_name: string | null;
};

export type ResponsibleSuggestions = {
  tag_name: string;
  open: ResponsibleSuggestion[];
  unmatched: ResponsibleSuggestion[];
  ignored: ResponsibleSuggestion[];
};

export type Me = {
  signed_in: boolean;
  oid: string | null;
  name: string | null;
  is_admin: boolean;
  responsible_for: { scope_id: string; name: string | null; level: ScopeLevel }[];
};

export type ReviewDecisionKind = "keep" | "remove" | "change" | "ask";

export type ReviewProgress = {
  total: number;
  keep: number;
  remove: number;
  change: number;
  ask: number;
  open: number;
  gone: number;
};

export type ReviewRound = {
  id: number;
  /** Null for the admins' round. */
  scope_id: string | null;
  scope_name: string | null;
  scope_level: ScopeLevel | null;
  period: string;
  reviewer_id: string | null;
  reviewer_name: string | null;
  opened_at: string;
  due_at: string;
  finished_at: string | null;
  finished_by_name: string | null;
  progress: ReviewProgress;
  /** Undecided or still asked, grant still there. */
  blocking: number;
  can_decide: boolean;
};

export type ReviewItem = {
  id: number;
  grant_id: string;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  principal_id: string;
  principal_name: string | null;
  principal_type: string | null;
  role_definition_id: string;
  role_name: string | null;
  level: CapabilityLevel;
  moved_from_scope: string | null;
  moved_from_name: string | null;
  decision: ReviewDecisionKind | null;
  /** The playbook's advice while undecided: the decision it suggests, its rule and title. */
  suggested?: ReviewDecisionKind | null;
  suggested_rule?: string | null;
  suggested_title?: string | null;
  note: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  carried: boolean;
  ask_remind_at: string | null;
  gone_at: string | null;
  remove_command: string;
};

export type ReviewRoundDetail = ReviewRound & { items: ReviewItem[] };

// ---- to-dos (core/todos.py) -----------------------------------------------------------------

export type TodoStatus = "open" | "done" | "dismissed";

export type Todo = {
  id: number;
  round_id: number;
  period: string | null;
  item_id: number;
  grant_id: string;
  action: "remove" | "change";
  scope: string;
  scope_name: string | null;
  principal_id: string;
  principal_name: string | null;
  principal_type: string | null;
  role_name: string | null;
  note: string | null;
  advice_rule: string | null;
  how: string[];
  commands: string[];
  reviewer_name: string | null;
  decided_by_name: string | null;
  created_at: string;
  due_at: string;
  status: TodoStatus;
  overdue: boolean;
  done_at: string | null;
  dismissed_reason: string | null;
  dismissed_by_name: string | null;
  /** Change: roles the same identity now holds on the same place. */
  replacements: string[];
  can_dismiss: boolean;
};

export type TodoSummary = { open: number; overdue: number; mine_open: number; mine_overdue: number };

export type ReviewRef = {
  round_id: number;
  item_id: number;
  period: string;
  reviewer_name: string | null;
  decision: ReviewDecisionKind | null;
  can_decide: boolean;
};

export type NotificationKind = "ask" | "review_opened" | "review_due" | "reminder" | "todo_overdue" | "data_stale";

export type OutboxNotification = {
  id: number;
  kind: NotificationKind;
  recipient_id: string | null;
  recipient_name: string | null;
  subject_ref: string;
  payload: Record<string, unknown>;
  created_at: string;
  sent_at: string | null;
  channel: string | null;
  error: string | null;
};

export type NotificationStatus = {
  connected: boolean;
  channel: string | null;
  sender: string | null;
  admin_email: string | null;
  links: boolean;
  inbox_url: string | null;
  pending: number;
  sent: number;
  skipped: number;
  failed_last: string | null;
  by_kind: Partial<Record<NotificationKind, number>>;
};

export type GrantPower = { key: CapabilityLevel; label: string; allowed: boolean; detail: string | null };

// ---- advice (core/advice.py) ---------------------------------------------------------------

export type AdviceWeight = "must" | "should" | "could" | "info";
export type AdviceAction = "remove" | "pim" | "replace" | "narrow" | "ask" | "fix_members" | "fix_owners" | "set_responsible" | "keep";
export type SuggestedDecision = "keep" | "remove" | "change" | "ask";

export type AdviceNote = { rule: string; weight: AdviceWeight; title: string };

export type Advice = {
  rule: string;
  weight: AdviceWeight;
  action: AdviceAction;
  decision: SuggestedDecision | null;
  title: string;
  why: string;
  how: string[];
  commands: string[];
  controls: string[];
  also: AdviceNote[];
};

export type AdviceGrantRef = {
  grant_id: string;
  role_name: string | null;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  commands: string[];
};

export type IdentityAction = Omit<Advice, "commands" | "also"> & { grants: AdviceGrantRef[] };

export type IdentityAdvice = { actions: IdentityAction[]; more: number; fine: number };

export type AdviceSettings = { new_days: number; expiring_days: number; break_glass: string[] };
export type AdviceSettingsRead = AdviceSettings & {
  break_glass_names: { principal_id: string; name: string | null }[];
};

// ---- assessment report (core/report.py) -------------------------------------------------------

export type ControlStatus = "met" | "partly" | "not_met";

export type ReportControl = { control: string; name: string; status: ControlStatus; evidence: string[] };

export type ReportGrant = {
  grant_id: string;
  principal_id: string;
  principal_name: string | null;
  kind: "person" | "group" | "app" | "other";
  role_name: string | null;
  level: CapabilityLevel;
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  how: "standing" | "pim" | "time-bound";
  advice_rule: string;
  advice_title: string;
  advice_weight: AdviceWeight;
};

export type ReportRecommendation = {
  rule: string;
  weight: AdviceWeight;
  title: string;
  sentence: string;
  count: number;
  controls: string[];
  grants: ReportGrant[];
};

export type ReportReviews = {
  subscriptions: number;
  subscriptions_with_responsible: number;
  without_responsible: string[];
  rounds_this_period: number;
  items_this_period: number;
  decided_this_period: number;
  last_period: string | null;
  last_period_on_time: boolean | null;
  todos_open: number;
  todos_overdue: number;
  todos_done: number;
  todos_avg_days_to_close: number | null;
};

export type ReportChanges = {
  days: number;
  given: number;
  removed: number;
  powerful_given: number;
  unexplained: number;
  check_age_hours: number | null;
  subscriptions_collected_hourly: number;
  activity_gap_hours: number | null;
  subscriptions_not_collected: string[];
};

export type ReportNumbers = Record<string, number | null>;

export type Report = {
  scope_id: string | null;
  scope_name: string;
  scope_level: ScopeLevel | null;
  generated_at: string;
  access_as_of: string | null;
  identities: number;
  grants: number;
  summary: string[];
  controls: ReportControl[];
  privileged: ReportGrant[];
  break_glass: string[];
  microsoft_apps: number;
  identities_section: { orphans: ReportGrant[]; guests: ReportGrant[]; unknown_members: ReportGrant[] };
  least_privilege: ReportRecommendation[];
  reviews: ReportReviews;
  changes: ReportChanges;
  recommendations: ReportRecommendation[];
  numbers: ReportNumbers;
};

export type Assessment = {
  id: number;
  scope_id: string | null;
  scope_name: string;
  created_at: string;
  created_by_name: string | null;
  access_as_of: string | null;
  note: string | null;
  statuses: Record<string, ControlStatus>;
  numbers: ReportNumbers;
};

// ---- optional AI wording (core/ai_wording.py) -------------------------------------------------

export type AiSettingsRead = {
  enabled: boolean;
  model: string;
  models: string[];
  key_source: "stored" | "environment" | null;
  /** New texts per day (UTC) at most; 0 = no limit. */
  daily_limit?: number;
  written_today?: number;
  encryption_available: boolean;
};
export type AiTarget = { grant_id: string } | { principal_id: string };
export type AiPreview = { model: string; system: string; facts: Record<string, unknown> };
export type AiExplanation = { text: string; model: string; cached: boolean };

export type GrantDetail = GrantOrigin & {
  condition_kind?: ConditionKind | null;
  condition_text?: string | null;
  assignment_id: number;
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  kind: IdentityKind;
  member_count: number | null;
  role_name: string | null;
  role_definition_id: string;
  role_type: string | null;
  level: CapabilityLevel;
  powers: GrantPower[];
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  ancestors: ScopeCrumb[];
  inside_count: number;
  activated: boolean;
  until: string | null;
  access_as_of: string | null;
  last_activity_at: string | null;
  window_days: number | null;
  observed_op_count: number | null;
  top_operations: { operation: string; display: string; count: number }[];
  findings: CurrentFinding[];
  review?: ReviewRef | null;
  advice?: Advice | null;
  /** An app's or group's grant: who owns it; null = unknown or not an app or group. */
  owners?: OwnerRef[] | null;
  owners_elsewhere?: boolean;
};

/** Scope ids are ARM paths; the API takes them without the leading slash. */
function scopeParam(scopeId: string): string {
  return encodeURIComponent(scopeId.replace(/^\/+/, ""));
}

export const api = {
  health: () => request<HealthResponse>("/health"),

  settings: {
    getAdvice: () => request<AdviceSettingsRead>("/settings/advice"),
    putAdvice: (payload: AdviceSettings) =>
      request<AdviceSettingsRead>("/settings/advice", { method: "PUT", body: JSON.stringify(payload) }),
    getCredentials: () => request<CredentialsRead>("/settings/credentials"),
    putCredentials: (payload: CredentialsWrite) =>
      request<CredentialsRead>("/settings/credentials", {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    deleteCredentials: () =>
      request<CredentialsRead>("/settings/credentials", { method: "DELETE" }),

    getAuth: () => request<AuthSettingsRead>("/settings/auth"),
    putAuth: (payload: AuthSettingsWrite) =>
      request<AuthSettingsRead>("/settings/auth", {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
  },

  auth: {
    getConfig: () => request<AuthConfigPublic>("/auth/config"),
  },

  audit: {
    listEvents: (limit = 100, offset = 0) =>
      request<AuditEvent[]>(
        `/audit/events?limit=${limit}&offset=${offset}`,
      ),
  },

  scopeBrowser: {
    subscriptions: (mgmtGroup?: string) =>
      request<SubscriptionNode[]>(
        `/scope-browser/subscriptions${mgmtGroup ? `?mgmt_group=${encodeURIComponent(mgmtGroup)}` : ""}`,
      ),
    refresh: () =>
      request<void>("/scope-browser/refresh", { method: "POST" }),
  },

  database: {
    getStats: () => request<DatabaseStats>("/settings/database/stats"),
    vacuum: (full = false) =>
      request<MaintenanceResult>(`/settings/database/vacuum${full ? "?full=true" : ""}`, { method: "POST" }),
    analyze: () =>
      request<MaintenanceResult>("/settings/database/analyze", { method: "POST" }),
    reindex: () =>
      request<MaintenanceResult>("/settings/database/reindex", { method: "POST" }),
    retentionPolicy: () => request<RetentionPolicy>("/settings/database/retention"),
    applyRetention: () =>
      request<RetentionRun>("/settings/database/retention", { method: "POST" }),
    dataKept: () => request<DataKept>("/settings/data-kept"),
    saveDataKept: (values: Record<string, number | null>) =>
      request<DataKept>("/settings/data-kept", { method: "PUT", body: JSON.stringify({ values }) }),
    previewDataKept: (values: Record<string, number | null>) =>
      request<{ deletes: Record<string, number> }>("/settings/data-kept/preview", {
        method: "POST",
        body: JSON.stringify({ values }),
      }),
  },

  activity: {
    listPulls: (subscriptionId?: string, limit = 50) => {
      const sp = new URLSearchParams();
      if (subscriptionId) sp.set("subscription_id", subscriptionId);
      sp.set("limit", String(limit));
      return request<ActivityPull[]>(`/activity/pulls?${sp.toString()}`);
    },
    startPull: (subscriptionId: string, start: string, end: string) =>
      request<ActivityPull>("/activity/pulls", {
        method: "POST",
        body: JSON.stringify({ subscription_id: subscriptionId, start, end }),
      }),
    deletePull: (id: number) =>
      request<void>(`/activity/pulls/${id}`, { method: "DELETE" }),
    listEvents: (filters: ActivityEventFilters = {}) => {
      const sp = activityFilterParams(filters);
      const qs = sp.toString();
      return request<ActivityEvent[]>(
        `/activity/events${qs ? `?${qs}` : ""}`,
      );
    },
    getEvent: (id: number) =>
      request<ActivityEventDetail>(`/activity/events/${id}`),
    exportEventsUrl: (
      fmt: "csv" | "json",
      filters: ActivityEventFilters = {},
    ) => {
      const sp = activityFilterParams(filters);
      sp.set("fmt", fmt);
      return `${BASE}/activity/events/export?${sp.toString()}`;
    },
  },

  iam: {
    getScan: (id: number) => request<Scan>(`/iam-access/scans/${id}`),
    startScan: (scope: string) =>
      request<Scan>("/iam-access/scans", {
        method: "POST",
        body: JSON.stringify({ scope }),
      }),
    deleteScan: (id: number) =>
      request<void>(`/iam-access/scans/${id}`, { method: "DELETE" }),
    getGroupMembers: (scanId: number, groupId: string) =>
      request<GroupMembersResult>(
        `/iam-access/scans/${scanId}/groups/${encodeURIComponent(groupId)}/members`,
      ),
    diffScans: (oldId: number, newId: number) =>
      request<ScanDiff>(`/iam-access/scans/${oldId}/diff/${newId}`),
    getRoleDefinition: (roleId: string) =>
      request<RoleDefinitionDetail>(
        `/iam-access/role-definitions/${encodeURIComponent(roleId)}`,
      ),
    listRoleDefinitions: (filters: RoleListFilters = {}) => {
      const sp = new URLSearchParams();
      if (filters.q) sp.set("q", filters.q);
      if (filters.role_type) sp.set("role_type", filters.role_type);
      if (filters.risk) sp.set("risk", filters.risk);
      if (filters.unused) sp.set("unused", "true");
      if (filters.sort) sp.set("sort", filters.sort);
      const qs = sp.toString();
      return request<RoleDefinitionListItem[]>(
        `/iam-access/role-definitions${qs ? `?${qs}` : ""}`,
      );
    },
    getRoleUsage: (roleId: string) =>
      request<RoleUsage>(
        `/iam-access/role-definitions/${encodeURIComponent(roleId)}/usage`,
      ),
    exportUrl: (
      id: number,
      fmt: "json" | "csv",
      filters?: RoleAssignmentFilters,
    ) => {
      const qs = new URLSearchParams({ fmt });
      if (filters?.principal_type) qs.set("principal_type", filters.principal_type);
      if (filters?.role_name) qs.set("role_name", filters.role_name);
      if (filters?.scope_level) qs.set("scope_level", filters.scope_level);
      return `${BASE}/iam-access/scans/${id}/export?${qs.toString()}`;
    },
  },

  overview: {
    get: () => request<Overview>("/dashboard/overview"),
  },

  scopes: {
    tree: (opts: { root?: string; includeResources?: boolean } = {}) => {
      const sp = new URLSearchParams();
      if (opts.root) sp.set("root", opts.root);
      if (opts.includeResources) sp.set("include_resources", "true");
      const qs = sp.toString();
      return request<ScopeNode[]>(`/scopes/tree${qs ? `?${qs}` : ""}`);
    },
    // Path segments are kept literal (ARM ids contain "/"), each one encoded.
    detail: (scopeId: string) =>
      request<ScopeDetail>(
        `/scopes/${scopeId.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/")}`,
      ),
    assignments: (scopeId: string) =>
      request<ScopeAssignments>(`/scopes/assignments?scope=${scopeParam(scopeId)}`),
    findings: (scopeId: string) =>
      request<ScopeFindings>(`/scopes/findings?scope=${scopeParam(scopeId)}`),
    history: (scopeId: string) =>
      request<ScopeSnapshot[]>(`/scopes/history?scope=${scopeParam(scopeId)}`),
    activity: (scopeId: string) =>
      request<ScopeActivity>(`/scopes/activity?scope=${scopeParam(scopeId)}`),
    capabilities: (scopeId: string) =>
      request<ScopeCapabilities>(`/scopes/capabilities?scope=${scopeParam(scopeId)}`),
  },

  principals: {
    list: (params: { q?: string; principal_type?: string; level?: CapabilityLevel; limit?: number } = {}) => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== "") sp.set(k, String(v));
      }
      const qs = sp.toString();
      return request<RegistryPrincipal[]>(`/principals${qs ? `?${qs}` : ""}`);
    },
    detail: (principalId: string) =>
      request<PrincipalPage>(`/principals/${encodeURIComponent(principalId)}`),
    activity: (principalId: string, days = 90) =>
      request<PrincipalActivity>(
        `/principals/${encodeURIComponent(principalId)}/activity?days=${days}`,
      ),
  },

  grants: {
    detail: (grantId: string) => request<GrantDetail>(`/grants/${encodeURIComponent(grantId)}`),
  },

  changes: {
    list: (params: ChangesParams = {}) => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== "" && v !== false) sp.set(k, String(v));
      }
      const qs = sp.toString();
      return request<ChangesResponse>(`/changes${qs ? `?${qs}` : ""}`);
    },
  },

  freshness: () => request<Freshness>("/freshness"),

  me: () => request<Me>("/me"),

  notifications: {
    status: () => request<NotificationStatus>("/notifications/status"),
    list: (limit = 50) => request<OutboxNotification[]>(`/notifications?limit=${limit}`),
    test: (to: string) =>
      request<{ sent_to: string }>("/notifications/test", { method: "POST", body: JSON.stringify({ to }) }),
  },

  reviews: {
    list: (params: { mine?: boolean; period?: string } = {}) => {
      const sp = new URLSearchParams();
      if (params.mine) sp.set("mine", "true");
      if (params.period) sp.set("period", params.period);
      const qs = sp.toString();
      return request<ReviewRound[]>(`/reviews${qs ? `?${qs}` : ""}`);
    },
    detail: (id: number) => request<ReviewRoundDetail>(`/reviews/${id}`),
    decide: (id: number, itemId: number, decision: ReviewDecisionKind | null, note?: string | null) =>
      request<ReviewItem>(`/reviews/${id}/items/${itemId}`, {
        method: "PUT",
        body: JSON.stringify({ decision, note: note ?? null }),
      }),
    finish: (id: number) => request<ReviewRound>(`/reviews/${id}/finish`, { method: "POST" }),
    acceptAdvice: (id: number) =>
      request<Record<ReviewDecisionKind, number>>(`/reviews/${id}/accept-advice`, { method: "POST" }),
    exportUrl: (id: number) => `${BASE}/reviews/${id}/export`,
    open: () => request<Record<string, number>>("/reviews/open", { method: "POST" }),
  },

  ai: {
    status: () => request<{ available: boolean; model: string | null }>("/ai/status"),
    preview: (target: AiTarget) => request<AiPreview>("/ai/preview", { method: "POST", body: JSON.stringify(target) }),
    explain: (target: AiTarget) =>
      request<AiExplanation>("/ai/explain", { method: "POST", body: JSON.stringify(target) }),
    settings: () => request<AiSettingsRead>("/settings/ai"),
    saveSettings: (payload: { enabled: boolean; model: string; api_key?: string | null }) =>
      request<AiSettingsRead>("/settings/ai", { method: "PUT", body: JSON.stringify(payload) }),
  },

  report: {
    get: (scope?: string | null) => request<Report>(`/report${scope ? `?scope=${scopeParam(scope)}` : ""}`),
    annexUrl: (scope?: string | null) => `${BASE}/report/annex${scope ? `?scope=${scopeParam(scope)}` : ""}`,
    assessments: (scope?: string | null) =>
      request<Assessment[]>(`/report/assessments${scope ? `?scope=${scopeParam(scope)}` : ""}`),
    save: (scope: string | null, note: string | null) =>
      request<Assessment>("/report/assessments", { method: "POST", body: JSON.stringify({ scope, note }) }),
  },

  todos: {
    list: (params: { status?: TodoStatus | "all"; show?: "mine" | "all"; roundId?: number } = {}) => {
      const sp = new URLSearchParams();
      if (params.status) sp.set("status", params.status);
      if (params.show) sp.set("show", params.show);
      if (params.roundId) sp.set("round_id", String(params.roundId));
      const qs = sp.toString();
      return request<Todo[]>(`/todos${qs ? `?${qs}` : ""}`);
    },
    summary: () => request<TodoSummary>("/todos/summary"),
    dismiss: (id: number, reason: string) =>
      request<Todo>(`/todos/${id}/dismiss`, { method: "POST", body: JSON.stringify({ reason }) }),
    exportUrl: (show: "mine" | "all") => `${BASE}/todos/export?show=${show}`,
    script: (show: "mine" | "all") => requestText(`/todos/script?show=${show}`),
  },

  responsible: {
    tree: () => request<ResponsibleNode[]>("/responsible/tree"),
    of: (scopeId: string) => request<ResponsibleOf>(`/responsible/of?scope=${scopeParam(scopeId)}`),
    set: (scopeId: string, person: string) =>
      request<ResponsibleOf>(`/responsible?scope=${scopeParam(scopeId)}`, {
        method: "PUT",
        body: JSON.stringify({ person }),
      }),
    clear: (scopeId: string) =>
      request<void>(`/responsible?scope=${scopeParam(scopeId)}`, { method: "DELETE" }),
    suggestions: () => request<ResponsibleSuggestions>("/responsible/suggestions"),
    use: (scopeId: string) =>
      request<ResponsibleOf>("/responsible/suggestions/use", {
        method: "POST",
        body: JSON.stringify({ scope: scopeId }),
      }),
    ignore: (scopeId: string) =>
      request<void>("/responsible/suggestions/ignore", { method: "POST", body: JSON.stringify({ scope: scopeId }) }),
    saveSettings: (tagName: string) =>
      request<{ tag_name: string }>("/responsible/settings", {
        method: "PUT",
        body: JSON.stringify({ tag_name: tagName }),
      }),
  },

  search: (q: string, signal?: AbortSignal) =>
    request<SearchResults>(`/search?q=${encodeURIComponent(q)}`, { signal }),

  jobs: {
    list: (limit = 50, filter: JobFilter = {}) => {
      const sp = new URLSearchParams({ limit: String(limit) });
      if (filter.kind) sp.set("kind", filter.kind);
      if (filter.status) sp.set("status", filter.status);
      return request<Job[]>(`/jobs?${sp}`);
    },
    schedules: () => request<JobSchedule[]>("/schedules"),
    coverage: () => request<Coverage>("/activity/coverage"),
    checkEntraAudit: () => request<EntraAudit>("/activity/entra-audit/check", { method: "POST" }),
    createSchedule: (body: JobScheduleCreate) =>
      request<JobSchedule>("/schedules", { method: "POST", body: JSON.stringify(body) }),
    updateSchedule: (id: number, body: JobScheduleUpdate) =>
      request<JobSchedule>(`/schedules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    deleteSchedule: (id: number) =>
      request<void>(`/schedules/${id}`, { method: "DELETE" }),
    runSchedule: (id: number) =>
      request<JobSchedule>(`/schedules/${id}/run`, { method: "POST" }),
  },

  findings: {
    list: (params: FindingListParams = {}) => {
      const sp = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
      }
      const qs = sp.toString();
      return request<CurrentFindings>(`/findings${qs ? `?${qs}` : ""}`);
    },
    decideMany: (fingerprints: string[], body: DecisionWrite) =>
      request<{ updated: number }>("/iam-access/findings/decisions", {
        method: "POST",
        body: JSON.stringify({ ...body, fingerprints }),
      }),
    recompute: (scanId: number) =>
      request<unknown>(`/iam-access/scans/${scanId}/findings/recompute`, { method: "POST" }),
    decide: (fingerprint: string, body: DecisionWrite) =>
      request<ReviewDecision>(`/iam-access/findings/${encodeURIComponent(fingerprint)}/decision`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    reopen: (fingerprint: string) =>
      request<void>(`/iam-access/findings/${encodeURIComponent(fingerprint)}/decision`, {
        method: "DELETE",
      }),
  },
};
