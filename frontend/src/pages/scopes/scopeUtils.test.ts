import { describe, expect, test } from "vitest";

import type { ScopeAssignmentRow, ScopeNode } from "@/api/client";

import {
  ageLabel,
  allIds,
  coverageTone,
  DEFAULT_ASSIGNMENT_FILTER,
  expandToDepth,
  filterAssignments,
  filterTree,
  freshnessOf,
  isBroadHighRisk,
  parseTab,
  reviewProgress,
  scopeHref,
  scopeIdFromSplat,
  scopeLabel,
  snoozeUntil,
  treeLabel,
  visibleTreeRows,
  worstOpenSeverity,
} from "./scopeUtils";

const SUB = "/subscriptions/1111";

function row(overrides: Partial<ScopeAssignmentRow> = {}): ScopeAssignmentRow {
  return {
    assignment_id: 1,
    scan_id: 41,
    source: "direct",
    scope: SUB,
    scope_name: "prod",
    scope_level: "subscription",
    principal_id: "p-1",
    principal_name: "alice@example.com",
    principal_type: "User",
    role_name: "Reader",
    role_definition_id: "/providers/x/roleDefinitions/r",
    role_type: "BuiltInRole",
    role_risk: null,
    coverage_pct: null,
    granted_pattern_count: null,
    observed_op_count: null,
    member_count: null,
    covered_by_count: 0,
    findings: [],
    ...overrides,
  };
}

function node(id: string, children: ScopeNode[] = [], name: string | null = null): ScopeNode {
  return {
    id,
    name,
    level: "mgmt_group",
    parent_id: null,
    resource_type: null,
    latest_scan: null,
    stats: { direct_assignments: 0, assignments_below: 0, high_risk: 0, open_findings: 0 },
    children,
  };
}

describe("routing", () => {
  test("scopeHref omits the default tab and strips the leading slash", () => {
    expect(scopeHref(SUB)).toBe("/places/subscriptions/1111");
    expect(scopeHref(SUB, "who")).toBe("/places/subscriptions/1111");
    expect(scopeHref(SUB, "access")).toBe("/places/subscriptions/1111?tab=access");
    expect(scopeHref(SUB, "findings")).toBe("/places/subscriptions/1111?tab=findings");
  });

  test("scopeHref carries an encoded Access prefilter", () => {
    expect(scopeHref(SUB, "who", "Owner & co")).toBe("/places/subscriptions/1111?q=Owner+%26+co");
    expect(scopeHref(SUB, "findings", "x")).toBe("/places/subscriptions/1111?tab=findings&q=x");
    expect(scopeHref(SUB, "who", null)).toBe("/places/subscriptions/1111");
  });

  test("scopeIdFromSplat lowercases, trims slashes, and rejects empty", () => {
    expect(scopeIdFromSplat("subscriptions/AbC/resourceGroups/RG/")).toBe(
      "/subscriptions/abc/resourcegroups/rg",
    );
    expect(scopeIdFromSplat("")).toBeNull();
    expect(scopeIdFromSplat(undefined)).toBeNull();
  });

  test("parseTab falls back to who", () => {
    expect(parseTab("history")).toBe("history");
    expect(parseTab("access")).toBe("access");
    expect(parseTab("bogus")).toBe("who");
    expect(parseTab(null)).toBe("who");
  });

  test("treeLabel drops the subscription prefix for RGs and resources only", () => {
    expect(treeLabel(`${SUB}/resourcegroups/rg-app`, "prod / rg-app", "resource_group")).toBe("rg-app");
    expect(treeLabel(`${SUB}/resourcegroups/rg/x`, "prod / rg / stacct", "resource")).toBe("stacct");
    expect(treeLabel(SUB, "prod / weird", "subscription")).toBe("prod / weird");
    expect(treeLabel(`${SUB}/resourcegroups/rg-app`, null, "resource_group")).toBe("rg-app");
  });

  test("scopeLabel prefers the name, else the last segment", () => {
    expect(scopeLabel(SUB, "prod")).toBe("prod");
    expect(scopeLabel(`${SUB}/resourcegroups/rg-app`, null)).toBe("rg-app");
  });
});

describe("freshness", () => {
  test("bands around the window", () => {
    expect(freshnessOf(null)).toBe("never");
    expect(freshnessOf(0)).toBe("fresh");
    expect(freshnessOf(30)).toBe("fresh");
    expect(freshnessOf(31)).toBe("aging");
    expect(freshnessOf(60)).toBe("aging");
    expect(freshnessOf(61)).toBe("stale");
    expect(freshnessOf(10, 7)).toBe("aging");
  });

  test("ageLabel", () => {
    expect(ageLabel(null)).toBe("never checked");
    expect(ageLabel(0)).toBe("today");
    expect(ageLabel(1)).toBe("1 day old");
    expect(ageLabel(31)).toBe("31 days old");
  });
});

describe("assignments", () => {
  test("isBroadHighRisk needs a high-risk role at MG or subscription", () => {
    expect(isBroadHighRisk({ role_risk: "high", scope_level: "subscription" })).toBe(true);
    expect(isBroadHighRisk({ role_risk: "high", scope_level: "resource_group" })).toBe(false);
    expect(isBroadHighRisk({ role_risk: null, scope_level: "mgmt_group" })).toBe(false);
  });

  test("filterAssignments combines source, query, risk and open-findings filters", () => {
    const rows = [
      row({ assignment_id: 1, source: "direct", principal_name: "alice@example.com" }),
      row({
        assignment_id: 2,
        source: "inherited",
        principal_name: "sp-terraform",
        role_name: "Owner",
        role_risk: "high",
        scope_level: "mgmt_group",
        findings: [
          { finding_id: 9, fingerprint: "f", category: "unused", severity: "high", status: "open" },
        ],
      }),
      row({
        assignment_id: 3,
        source: "below",
        role_name: "Contributor",
        findings: [
          { finding_id: 8, fingerprint: "g", category: "unused", severity: "low", status: "reviewed" },
        ],
      }),
    ];
    const ids = (f: Partial<typeof DEFAULT_ASSIGNMENT_FILTER>) =>
      filterAssignments(rows, { ...DEFAULT_ASSIGNMENT_FILTER, ...f }).map((r) => r.assignment_id);

    expect(ids({})).toEqual([1, 2, 3]);
    expect(ids({ source: "inherited" })).toEqual([2]);
    expect(ids({ query: "TERRA" })).toEqual([2]);
    expect(ids({ query: "contrib" })).toEqual([3]);
    expect(ids({ highRiskOnly: true })).toEqual([2]);
    expect(ids({ withFindingsOnly: true })).toEqual([2]); // reviewed finding doesn't count
  });

  test("coverageTone and worstOpenSeverity", () => {
    expect(coverageTone(null)).toBe("unknown");
    expect(coverageTone(0)).toBe("none");
    expect(coverageTone(30)).toBe("low");
    expect(coverageTone(80)).toBe("ok");
    const r = row({
      findings: [
        { finding_id: 1, fingerprint: "a", category: "x", severity: "low", status: "open" },
        { finding_id: 2, fingerprint: "b", category: "x", severity: "high", status: "accepted_risk" },
        { finding_id: 3, fingerprint: "c", category: "x", severity: "medium", status: "open" },
      ],
    });
    expect(worstOpenSeverity(r)).toBe("medium");
    expect(worstOpenSeverity(row())).toBeNull();
  });
});

describe("review", () => {
  test("snoozeUntil adds whole days", () => {
    expect(snoozeUntil(30, new Date("2026-10-22T00:00:00Z"))).toBe("2026-11-21T00:00:00.000Z");
  });

  test("reviewProgress counts everything not open as done", () => {
    expect(
      reviewProgress({ total: 8, open: 6, by_severity: {}, by_category: {}, by_status: {} }),
    ).toEqual({ done: 2, total: 8, pct: 25 });
    expect(
      reviewProgress({ total: 0, open: 0, by_severity: {}, by_category: {}, by_status: {} }).pct,
    ).toBe(0);
  });
});

describe("tree", () => {
  const tree = [
    node("/mg/root", [node("/mg/a", [node("/sub/1", [], "prod")]), node("/mg/b")], "Root"),
  ];

  test("visibleTreeRows only descends into expanded nodes", () => {
    const collapsed = visibleTreeRows(tree, new Set());
    expect(collapsed.map((r) => r.node.id)).toEqual(["/mg/root"]);
    expect(collapsed[0]).toMatchObject({ hasChildren: true, expanded: false });

    const open = visibleTreeRows(tree, new Set(["/mg/root", "/mg/a"]));
    expect(open.map((r) => [r.node.id, r.depth])).toEqual([
      ["/mg/root", 0],
      ["/mg/a", 1],
      ["/sub/1", 2],
      ["/mg/b", 1],
    ]);
    expect(open[3]).toMatchObject({ hasChildren: false, expanded: false });
  });

  test("expandToDepth opens parents down to the given depth", () => {
    expect([...expandToDepth(tree, 1)]).toEqual(["/mg/root"]);
    expect([...expandToDepth(tree, 2)].sort()).toEqual(["/mg/a", "/mg/root"]);
  });

  test("filterTree keeps matches and their ancestors", () => {
    expect(filterTree(tree, "  ")).toBeNull();
    const hit = filterTree(tree, "PROD")!;
    expect([...allIds(hit)].sort()).toEqual(["/mg/a", "/mg/root", "/sub/1"]);
    expect(filterTree(tree, "nothing-matches")).toEqual([]);
  });
});
