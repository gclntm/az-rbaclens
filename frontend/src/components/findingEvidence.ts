/**
 * Typed view over a finding's ``evidence`` JSON (see backend
 * app/core/findings.py). Every field is optional there — evidence shape
 * depends on the category — so this parser is defensive and never throws.
 */

export type Evidence = {
  usage: { coveragePct: number; granted: number; used: number; observedOps: number } | null;
  unusedPatterns: string[];
  topOperations: { name: string; count: number }[];
  recommendations: { roleName: string; roleDefinitionId: string; patterns: number; saved: number }[];
  coveredBy: { scope: string; scopeName: string | null; roleName: string | null }[];
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

export function parseEvidence(raw: Record<string, unknown> | null | undefined): Evidence {
  const e = obj(raw);
  const pct = num(e.coverage_pct);
  const granted = num(e.granted_pattern_count);
  return {
    usage:
      pct !== null && granted !== null
        ? {
            coveragePct: pct,
            granted,
            used: num(e.used_pattern_count) ?? 0,
            observedOps: num(e.observed_op_count) ?? 0,
          }
        : null,
    unusedPatterns: arr(e.unused_patterns).map(str).filter((x): x is string => x !== null),
    topOperations: arr(e.top_operations)
      .map((o) => ({ name: str(obj(o).operation_name), count: num(obj(o).count) }))
      .filter((o): o is { name: string; count: number } => o.name !== null && o.count !== null),
    recommendations: arr(e.recommendations)
      .map((r) => {
        const x = obj(r);
        return {
          roleName: str(x.role_name) ?? str(x.role_definition_id) ?? "",
          roleDefinitionId: str(x.role_definition_id) ?? "",
          patterns: num(x.granted_pattern_count) ?? 0,
          saved: num(x.patterns_saved) ?? 0,
        };
      })
      .filter((r) => r.roleDefinitionId),
    coveredBy: arr(e.covered_by)
      .map((c) => {
        const x = obj(c);
        return { scope: str(x.scope) ?? "", scopeName: str(x.scope_name), roleName: str(x.role_name) };
      })
      .filter((c) => c.scope),
  };
}

export function hasEvidence(e: Evidence): boolean {
  return (
    e.usage !== null ||
    e.unusedPatterns.length > 0 ||
    e.topOperations.length > 0 ||
    e.recommendations.length > 0 ||
    e.coveredBy.length > 0
  );
}
