import { describe, expect, test } from "vitest";

import { hasEvidence, parseEvidence } from "./findingEvidence";

describe("parseEvidence", () => {
  test("reads usage, patterns, operations and recommendations", () => {
    const e = parseEvidence({
      role_risk: "high",
      coverage_pct: 12,
      granted_pattern_count: 8,
      used_pattern_count: 1,
      observed_op_count: 4,
      unused_patterns: ["*/write", "microsoft.storage/*"],
      top_operations: [{ operation_name: "Microsoft.Compute/virtualMachines/read", count: 40 }],
      recommendations: [
        { role_definition_id: "acdd", role_name: "Reader", granted_pattern_count: 1, patterns_saved: 7 },
      ],
    });
    expect(e.usage).toEqual({ coveragePct: 12, granted: 8, used: 1, observedOps: 4 });
    expect(e.unusedPatterns).toEqual(["*/write", "microsoft.storage/*"]);
    expect(e.topOperations).toEqual([{ name: "Microsoft.Compute/virtualMachines/read", count: 40 }]);
    expect(e.recommendations).toEqual([
      { roleName: "Reader", roleDefinitionId: "acdd", patterns: 1, saved: 7 },
    ]);
    expect(hasEvidence(e)).toBe(true);
  });

  test("reads covered_by for redundant findings", () => {
    const e = parseEvidence({
      covered_by: [{ assignment_id: 1, scope: "/mg/root", scope_name: "Root", role_name: "Owner" }],
    });
    expect(e.coveredBy).toEqual([{ scope: "/mg/root", scopeName: "Root", roleName: "Owner" }]);
    expect(e.usage).toBeNull();
  });

  test("is defensive about missing or malformed fields", () => {
    expect(hasEvidence(parseEvidence(null))).toBe(false);
    const e = parseEvidence({
      coverage_pct: "12",
      unused_patterns: "nope",
      top_operations: [{ operation_name: 3 }, null],
      recommendations: [{ role_name: "x" }],
      covered_by: [{}],
    });
    expect(e.usage).toBeNull();
    expect(e.unusedPatterns).toEqual([]);
    expect(e.topOperations).toEqual([]);
    expect(e.recommendations).toEqual([]);
    expect(e.coveredBy).toEqual([]);
    expect(hasEvidence(e)).toBe(false);
  });
});
