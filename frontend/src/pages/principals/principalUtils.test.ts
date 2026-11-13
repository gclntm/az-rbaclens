import { describe, expect, it } from "vitest";

import type { PrincipalAccessRow } from "@/api/client";

import {
  filterAccess,
  groupBySubscription,
  parsePrincipalTab,
  principalHref,
  typeLabel,
  usageLabel,
  usageTitle,
} from "./principalUtils";

function row(over: Partial<PrincipalAccessRow>): PrincipalAccessRow {
  return {
    assignment_id: 1,
    scan_id: 1,
    scope: "/subscriptions/s1",
    scope_name: "prod",
    scope_level: "subscription",
    level: "view",
    subscription_id: "s1",
    subscription_name: "prod",
    role_name: "Reader",
    role_definition_id: "r",
    role_type: "BuiltInRole",
    role_risk: null,
    is_high_risk: false,
    source: "direct",
    via_group_id: null,
    via_group_name: null,
    observed_op_count: null,
    coverage_pct: null,
    open_findings: 0,
    ...over,
  };
}

describe("principalHref / tabs / types", () => {
  it("lowercases ids and omits the default tab", () => {
    expect(principalHref("ABC")).toBe("/people/abc");
    expect(principalHref("abc", "activity")).toBe("/people/abc?tab=activity");
    expect(principalHref("abc", "access")).toBe("/people/abc");
  });
  it("parses unknown tabs as access", () => {
    expect(parsePrincipalTab("groups")).toBe("groups");
    expect(parsePrincipalTab("nope")).toBe("access");
    expect(parsePrincipalTab(null)).toBe("access");
  });
  it("labels types", () => {
    expect(typeLabel("ServicePrincipal")).toBe("App");
    expect(typeLabel("Other")).toBe("Other");
    expect(typeLabel(null)).toBe("Unknown type");
  });
});

describe("groupBySubscription", () => {
  it("puts MG / tenant grants first, then subscriptions by name, counting high risk", () => {
    const groups = groupBySubscription([
      row({ assignment_id: 1, subscription_id: "s2", subscription_name: "zeta" }),
      row({ assignment_id: 2, subscription_id: null, subscription_name: null, is_high_risk: true }),
      row({ assignment_id: 3, subscription_id: "s1", subscription_name: "alpha" }),
      row({ assignment_id: 4, subscription_id: "s2", subscription_name: "zeta", is_high_risk: true }),
    ]);
    expect(groups.map((g) => [g.label, g.rows.map((r) => r.assignment_id), g.highRisk])).toEqual([
      ["Management groups & tenant", [2], 1],
      ["alpha", [3], 0],
      ["zeta", [1, 4], 1],
    ]);
  });
});

describe("filterAccess", () => {
  const rows = [
    row({ assignment_id: 1 }),
    row({ assignment_id: 2, source: "group", via_group_name: "platform-admins", is_high_risk: true }),
  ];
  it("filters by source, risk and text (incl. group name)", () => {
    const ids = (f: Parameters<typeof filterAccess>[1]) => filterAccess(rows, f).map((r) => r.assignment_id);
    expect(ids({ source: "group", query: "", highRiskOnly: false })).toEqual([2]);
    expect(ids({ source: "all", query: "", highRiskOnly: true })).toEqual([2]);
    expect(ids({ source: "all", query: "PLATFORM", highRiskOnly: false })).toEqual([2]);
    expect(ids({ source: "all", query: "", highRiskOnly: false })).toEqual([1, 2]);
  });
});

describe("usageLabel", () => {
  it("distinguishes unknown, unused and used", () => {
    expect(usageLabel(row({}))).toBe("—");
    expect(usageLabel(row({ observed_op_count: 0 }))).toBe("no change recorded");
    expect(usageLabel(row({ observed_op_count: 4, coverage_pct: 0 }))).toBe("0 % used");
    expect(usageLabel(row({ observed_op_count: 3 }))).toBe("active");
    expect(usageTitle(row({ observed_op_count: 1, source: "group" }))).toBe(
      "1 kind of change by the group's members in the last 90 days, anywhere",
    );
    expect(usageTitle(row({ observed_op_count: 0 }))).toBeUndefined();
  });
});
