import type { CurrentFinding, FindingSeverity } from "@/api/client";
import { SEVERITY_ORDER } from "@/pages/scopes/scopeUtils";

/** Every finding on one grant, shown as one card. */
export type FindingGroup<F extends CurrentFinding> = {
  key: string;
  findings: F[];
  /** The worst severity among them. */
  severity: FindingSeverity;
  open: F[];
};

/**
 * Findings grouped by grant (scan + role assignment), in the order of each grant's first finding
 * (the API sorts worst first), so one grant flagged for several reasons is one card.
 */
export function groupByGrant<F extends CurrentFinding>(findings: F[]): FindingGroup<F>[] {
  const groups = new Map<string, FindingGroup<F>>();
  for (const f of findings) {
    const key = `${f.scan_id}-${f.role_assignment_id ?? f.fingerprint}`;
    let g = groups.get(key);
    if (!g) {
      g = { key, findings: [], severity: f.severity, open: [] };
      groups.set(key, g);
    }
    g.findings.push(f);
    if (f.status === "open") g.open.push(f);
    if (SEVERITY_ORDER[f.severity] < SEVERITY_ORDER[g.severity]) g.severity = f.severity;
  }
  return [...groups.values()];
}

/** "1 grant, 2 reasons" style counts for a list header. */
export function groupCountText(groups: { findings: unknown[] }[]): string {
  const reasons = groups.reduce((n, g) => n + g.findings.length, 0);
  const grants = groups.length;
  return `${grants} ${grants === 1 ? "grant" : "grants"}, ${reasons} ${reasons === 1 ? "reason" : "reasons"}`;
}
