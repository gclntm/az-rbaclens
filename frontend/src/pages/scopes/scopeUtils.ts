/**
 * Pure helpers for the Overview and Scope pages. No React, no fetches —
 * everything here is unit-tested in scopeUtils.test.ts.
 */

import type {
  AssignmentSource,
  FindingSeverity,
  FindingsSummary,
  ScopeAssignmentRow,
  ScopeLevel,
  ScopeNode,
} from "@/api/client";

/** ``who`` is the default; ``access`` is the full grants table ("All grants"). */
export type ScopeTab = "who" | "findings" | "history" | "activity" | "access";
export const SCOPE_TABS: ScopeTab[] = ["who", "findings", "history", "activity", "access"];

export const LEVEL_SHORT: Record<ScopeLevel, string> = {
  directory: "Entra",
  mgmt_group: "MG",
  subscription: "Sub",
  resource_group: "RG",
  resource: "Res",
};

export const LEVEL_LABEL: Record<ScopeLevel, string> = {
  directory: "Entra ID",
  mgmt_group: "Management group",
  subscription: "Subscription",
  resource_group: "Resource group",
  resource: "Resource",
};

export const CATEGORY_LABEL: Record<string, string> = {
  high_risk_broad_scope: "Powerful role given high up",
  unresolved_principal: "Identity no longer exists",
  unused: "No change in the window",
  over_broad: "Allows far more than was used",
  custom_replaceable: "Custom role a built-in one could replace",
  redundant: "Already covered by another grant",
  membership_unknown: "Group members unknown",
  drift_added: "New since the previous check",
  foreign_group: "Powerful role held from another tenant",
  entra_standing_admin: "Entra admin role held all the time",
  entra_app_privileged: "An app holds a powerful Entra role",
  entra_too_many_global_admins: "Too many Global Administrators",
  entra_role_via_group: "Powerful Entra role through a group",
  guest_owner: "A guest owns an app or group with power",
  app_owned_by_app: "Another app owns this app",
  app_owners: "People own this app and can act as it",
  group_owners: "People own this group and pick its members",
  entra_admin_idle: "Entra admin who hasn't signed in",
  app_secret_long_lived: "App secret valid for years",
  app_secret_expired: "Expired app secret still on file",
};

/** One sentence per category for the "Needs a look" list on Home. */
export const CATEGORY_SENTENCE: Record<string, string> = {
  high_risk_broad_scope: "A powerful role given high up, so it applies to everything below.",
  unresolved_principal: "Given to an identity that no longer exists in Entra ID.",
  unused: "No change recorded in the window. Reads are never recorded, so ask before removing.",
  over_broad: "The role allows far more than what was used.",
  custom_replaceable: "A custom role that a built-in role could replace.",
  redundant: "Already covered by another grant.",
  membership_unknown: "A group whose members could not be read.",
  drift_added: "New since the previous check.",
  foreign_group:
    "A group of another tenant (a partner, through delegated admin access) holds a powerful role; its members can't be seen from here.",
  entra_standing_admin: "A person holds an Entra role that can change who has access, all the time, not through PIM.",
  entra_app_privileged: "An app holds an Entra role that can change who has access or get in as someone else.",
  entra_too_many_global_admins: "More Global Administrators than the fewer than five Microsoft advises.",
  entra_role_via_group: "A powerful Entra role comes through a group: whoever manages its members decides who holds it.",
  guest_owner: "A guest from another organisation owns an app or group that can change who has access or get keys.",
  app_owned_by_app: "Another app owns this app: whoever controls that app can add a secret here and act as it.",
  app_owners: "People own this app: each can add a secret to it and use its powerful access.",
  group_owners: "People own this group: each can add anyone to it, themselves included, and so give its access.",
  entra_admin_idle: "Someone holds a powerful Entra role, all the time, and hasn't signed in for the idle threshold.",
  app_secret_long_lived: "An app with powerful access has a secret or certificate valid more than two years.",
  app_secret_expired: "An app with powerful access still has an expired secret or certificate.",
};

export const SEVERITY_ORDER: Record<FindingSeverity, number> = { high: 0, medium: 1, low: 2 };

export const SOURCE_LABEL: Record<AssignmentSource, string> = {
  direct: "Given here",
  inherited: "From above",
  below: "Inside",
};

// ---------------------------------------------------------------------------
// Routing — scope ids are lowercased ARM paths that start with "/".
// The SPA route is "/places" + id, e.g. /places/subscriptions/<guid>.
// ---------------------------------------------------------------------------

export function scopeHref(scopeId: string, tab?: ScopeTab, query?: string | null): string {
  const path = `/places/${scopeId.replace(/^\/+/, "")}`;
  const sp = new URLSearchParams();
  if (tab && tab !== "who") sp.set("tab", tab);
  if (query) sp.set("q", query);
  const qs = sp.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Router splat ("subscriptions/x/resourceGroups/y/") → canonical scope id. */
export function scopeIdFromSplat(splat: string | undefined): string | null {
  const trimmed = (splat ?? "").replace(/^\/+|\/+$/g, "");
  return trimmed ? `/${trimmed.toLowerCase()}` : null;
}

export function parseTab(value: string | null): ScopeTab {
  return SCOPE_TABS.includes(value as ScopeTab) ? (value as ScopeTab) : "who";
}

export function lastSegment(scopeId: string): string {
  return scopeId.replace(/\/+$/, "").split("/").pop() || scopeId;
}

export function scopeLabel(scopeId: string, name: string | null | undefined): string {
  return name || lastSegment(scopeId);
}

/**
 * Label inside a tree, where the parent is already visible. RG and resource
 * display names carry their subscription as a prefix ("prod / rg-app");
 * keep only the last " / " segment there.
 */
export function treeLabel(scopeId: string, name: string | null | undefined, level: ScopeLevel): string {
  const full = scopeLabel(scopeId, name);
  if (level !== "resource_group" && level !== "resource") return full;
  const parts = full.split(" / ");
  return parts[parts.length - 1] || full;
}

// ---------------------------------------------------------------------------
// Freshness
// ---------------------------------------------------------------------------

export type Freshness = "fresh" | "aging" | "stale" | "never";

/** ≤ window → fresh; ≤ 2 × window → aging; older → stale; no snapshot → never. */
export function freshnessOf(ageDays: number | null | undefined, windowDays = 30): Freshness {
  if (ageDays === null || ageDays === undefined) return "never";
  if (ageDays <= windowDays) return "fresh";
  if (ageDays <= windowDays * 2) return "aging";
  return "stale";
}

export function ageLabel(ageDays: number | null | undefined): string {
  if (ageDays === null || ageDays === undefined) return "never checked";
  if (ageDays === 0) return "today";
  if (ageDays === 1) return "1 day old";
  return `${ageDays} days old`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Assignments
// ---------------------------------------------------------------------------

const BROAD: ReadonlySet<ScopeLevel> = new Set(["directory", "mgmt_group", "subscription"]);

/** Same rule as the backend's ``is_high_risk``: high-risk role at MG / subscription. */
export function isBroadHighRisk(row: Pick<ScopeAssignmentRow, "role_risk" | "scope_level">): boolean {
  return row.role_risk === "high" && BROAD.has(row.scope_level);
}

export function principalLabel(row: { principal_name: string | null; principal_id: string }): string {
  return row.principal_name || row.principal_id;
}

export type AssignmentFilter = {
  source: AssignmentSource | "all";
  query: string;
  highRiskOnly: boolean;
  withFindingsOnly: boolean;
};

export const DEFAULT_ASSIGNMENT_FILTER: AssignmentFilter = {
  source: "all",
  query: "",
  highRiskOnly: false,
  withFindingsOnly: false,
};

export function filterAssignments(
  rows: ScopeAssignmentRow[],
  f: AssignmentFilter,
): ScopeAssignmentRow[] {
  const q = f.query.trim().toLowerCase();
  return rows.filter((r) => {
    if (f.source !== "all" && r.source !== f.source) return false;
    if (f.highRiskOnly && !isBroadHighRisk(r)) return false;
    if (f.withFindingsOnly && !r.findings.some((x) => x.status === "open")) return false;
    if (!q) return true;
    return [r.principal_name, r.principal_id, r.role_name, r.scope_name, r.scope]
      .filter(Boolean)
      .some((v) => (v as string).toLowerCase().includes(q));
  });
}

export type CoverageTone = "unknown" | "none" | "low" | "ok";

export function coverageTone(pct: number | null | undefined): CoverageTone {
  if (pct === null || pct === undefined) return "unknown";
  if (pct === 0) return "none";
  if (pct < 50) return "low";
  return "ok";
}

export function worstOpenSeverity(row: ScopeAssignmentRow): FindingSeverity | null {
  let worst: FindingSeverity | null = null;
  for (const f of row.findings) {
    if (f.status !== "open") continue;
    if (worst === null || SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[worst]) worst = f.severity;
  }
  return worst;
}

// ---------------------------------------------------------------------------
// Findings / review
// ---------------------------------------------------------------------------

export function snoozeUntil(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + days * 86_400_000).toISOString();
}

export function reviewProgress(summary: FindingsSummary): { done: number; total: number; pct: number } {
  const total = summary.total;
  const done = total - summary.open;
  return { done, total, pct: total ? Math.round((100 * done) / total) : 0 };
}

// ---------------------------------------------------------------------------
// Tree
// ---------------------------------------------------------------------------

export type TreeRow = { node: ScopeNode; depth: number; hasChildren: boolean; expanded: boolean };

/** Visible rows of a tree given the set of expanded ids, depth-first. */
export function visibleTreeRows(nodes: ScopeNode[], expanded: ReadonlySet<string>): TreeRow[] {
  const out: TreeRow[] = [];
  const walk = (list: ScopeNode[], depth: number) => {
    for (const node of list) {
      const hasChildren = node.children.length > 0;
      const isOpen = hasChildren && expanded.has(node.id);
      out.push({ node, depth, hasChildren, expanded: isOpen });
      if (isOpen) walk(node.children, depth + 1);
    }
  };
  walk(nodes, 0);
  return out;
}

/** Ids of every node down to ``maxDepth`` (0 = roots only) — the default expansion. */
export function expandToDepth(nodes: ScopeNode[], maxDepth: number): Set<string> {
  const out = new Set<string>();
  const walk = (list: ScopeNode[], depth: number) => {
    for (const n of list) {
      if (depth < maxDepth && n.children.length) {
        out.add(n.id);
        walk(n.children, depth + 1);
      }
    }
  };
  walk(nodes, 0);
  return out;
}

/** Keep nodes whose name/id matches ``query`` plus their ancestors; null = no filter. */
export function filterTree(nodes: ScopeNode[], query: string): ScopeNode[] | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const prune = (list: ScopeNode[]): ScopeNode[] =>
    list.flatMap((n) => {
      const kids = prune(n.children);
      const hit = (n.name ?? "").toLowerCase().includes(q) || n.id.includes(q);
      return hit || kids.length ? [{ ...n, children: kids }] : [];
    });
  return prune(nodes);
}

export function allIds(nodes: ScopeNode[]): Set<string> {
  const out = new Set<string>();
  const walk = (list: ScopeNode[]) => {
    for (const n of list) {
      out.add(n.id);
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
