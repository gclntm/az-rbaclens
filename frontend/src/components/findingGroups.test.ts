import { describe, expect, it } from "vitest";

import type { CurrentFinding } from "@/api/client";

import { groupByGrant, groupCountText } from "./findingGroups";

function f(over: Partial<CurrentFinding>): CurrentFinding {
  return {
    id: 1, scan_id: 50, role_assignment_id: 7, fingerprint: Math.random().toString(36), category: "unused",
    severity: "medium", title: "Owner → grp on mg", detail: "", suggestion: null, evidence: null,
    computed_at: "", status: "open", decision: null, scope: "/mg", scope_name: "mg", scope_level: "mgmt_group",
    principal_id: "g", principal_name: "grp", principal_type: "Group", role_name: "Owner", ...over,
  };
}

describe("groupByGrant", () => {
  it("puts every finding on one grant in one card, worst severity first", () => {
    const groups = groupByGrant([
      f({ category: "high_risk_broad_scope", severity: "high" }),
      f({ category: "unused", severity: "medium", status: "reviewed" }),
      f({ role_assignment_id: 8, category: "redundant", severity: "low" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].findings.map((x) => x.category)).toEqual(["high_risk_broad_scope", "unused"]);
    expect(groups[0].severity).toBe("high");
    expect(groups[0].open).toHaveLength(1);
    expect(groupCountText(groups)).toBe("2 grants, 3 reasons");
  });
});
