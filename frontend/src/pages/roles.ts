import type { RoleUsageAssignment, ScopeLevel } from "@/api/client";

export type PrincipalUsage = {
  principal_id: string;
  principal_name: string | null;
  principal_type: string;
  assignments: RoleUsageAssignment[];
};

/**
 * Group a flat list of role-usage assignments by principal for display.
 *
 * Preserves the input order within each principal bucket and the
 * principal-first-seen order across buckets.
 */
export function groupByPrincipal(
  assignments: RoleUsageAssignment[],
): PrincipalUsage[] {
  const map = new Map<string, PrincipalUsage>();
  for (const a of assignments) {
    let bucket = map.get(a.principal_id);
    if (!bucket) {
      bucket = {
        principal_id: a.principal_id,
        principal_name: a.principal_name,
        principal_type: a.principal_type,
        assignments: [],
      };
      map.set(a.principal_id, bucket);
    } else if (!bucket.principal_name && a.principal_name) {
      // Prefer any non-null name over a null one if we see both.
      bucket.principal_name = a.principal_name;
    }
    bucket.assignments.push(a);
  }
  return [...map.values()];
}

export type ScopeUsage = {
  scope: string;
  scope_name: string | null;
  scope_level: ScopeLevel;
  /** The latest scan that touched this scope for *any* principal.  */
  latest_scan_id: number;
  latest_scan_finished_at: string | null;
  /** Distinct principals at this scope, ordered first-seen. */
  principals: RoleUsageAssignment[];
};

function isNewerFinishedAt(a: string | null, b: string | null): boolean {
  if (a === b) return false;
  if (a == null) return false;
  if (b == null) return true;
  return a > b;
}

/**
 * Group role-usage assignments by scope.
 *
 * Each bucket lists the distinct principals that hold the role at that
 * scope, plus the *latest* scan (by ``scan_finished_at``, falling back
 * to scan_id when timestamps tie or are missing) that observed any
 * principal there. That makes the question "who has Owner on
 * sub-platform right now" answerable in one row even if four scans
 * captured the same grant.
 *
 * Buckets are ordered by latest-scan first, then by scope so reviewers
 * see freshly-scanned scopes at the top.
 */
export function groupByScope(
  assignments: RoleUsageAssignment[],
): ScopeUsage[] {
  const map = new Map<string, ScopeUsage>();
  for (const a of assignments) {
    let bucket = map.get(a.scope);
    if (!bucket) {
      bucket = {
        scope: a.scope,
        scope_name: a.scope_name,
        scope_level: a.scope_level,
        latest_scan_id: a.scan_id,
        latest_scan_finished_at: a.scan_finished_at,
        principals: [a],
      };
      map.set(a.scope, bucket);
      continue;
    }
    if (!bucket.scope_name && a.scope_name) bucket.scope_name = a.scope_name;
    if (
      isNewerFinishedAt(a.scan_finished_at, bucket.latest_scan_finished_at)
      || (a.scan_finished_at === bucket.latest_scan_finished_at
          && a.scan_id > bucket.latest_scan_id)
    ) {
      bucket.latest_scan_id = a.scan_id;
      bucket.latest_scan_finished_at = a.scan_finished_at;
    }
    // Distinct principals per scope. Backend already dedupes
    // (scan_root, principal, scope), but a principal may appear at
    // the same scope across multiple scan roots — we still want them
    // listed once per scope here.
    if (!bucket.principals.some((p) => p.principal_id === a.principal_id)) {
      bucket.principals.push(a);
    }
  }
  return [...map.values()].sort((a, b) => {
    if (isNewerFinishedAt(a.latest_scan_finished_at, b.latest_scan_finished_at)) {
      return -1;
    }
    if (isNewerFinishedAt(b.latest_scan_finished_at, a.latest_scan_finished_at)) {
      return 1;
    }
    return a.scope.localeCompare(b.scope);
  });
}
