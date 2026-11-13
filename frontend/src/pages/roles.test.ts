import { describe, expect, test } from "vitest";

import type { RoleUsageAssignment } from "@/api/client";

import { groupByPrincipal, groupByScope } from "./roles";

function ra(overrides: Partial<RoleUsageAssignment> = {}): RoleUsageAssignment {
  return {
    assignment_id: 1,
    scan_id: 1,
    scan_scope: "/sub/x",
    scan_scope_level: "subscription",
    scan_finished_at: "2026-05-01T00:00:00Z",
    scope: "/sub/x",
    scope_name: null,
    scope_level: "subscription",
    principal_id: "p-1",
    principal_name: "Alice",
    principal_type: "User",
    ...overrides,
  };
}

describe("groupByPrincipal", () => {
  test("buckets assignments by principal_id", () => {
    const rows = [
      ra({ assignment_id: 1, principal_id: "p-1" }),
      ra({ assignment_id: 2, principal_id: "p-2" }),
      ra({ assignment_id: 3, principal_id: "p-1" }),
    ];
    const groups = groupByPrincipal(rows);
    expect(groups).toHaveLength(2);
    expect(groups[0].principal_id).toBe("p-1");
    expect(groups[0].assignments.map((a) => a.assignment_id)).toEqual([1, 3]);
    expect(groups[1].principal_id).toBe("p-2");
  });

  test("prefers a non-null name when one variant of the principal has it", () => {
    const rows = [
      ra({ principal_id: "p-1", principal_name: null }),
      ra({ principal_id: "p-1", principal_name: "Alice" }),
    ];
    const groups = groupByPrincipal(rows);
    expect(groups[0].principal_name).toBe("Alice");
  });

  test("preserves first-seen order across principals", () => {
    const rows = [
      ra({ principal_id: "p-2" }),
      ra({ principal_id: "p-1" }),
      ra({ principal_id: "p-3" }),
    ];
    const groups = groupByPrincipal(rows);
    expect(groups.map((g) => g.principal_id)).toEqual(["p-2", "p-1", "p-3"]);
  });

  test("handles empty input", () => {
    expect(groupByPrincipal([])).toEqual([]);
  });
});

describe("groupByScope", () => {
  test("dedupes principals per scope across scan roots", () => {
    // Same principal observed at the same scope from two scan roots.
    const rows = [
      ra({ scope: "/sub/x", principal_id: "p-1" }),
      ra({ scope: "/sub/x", principal_id: "p-1", scan_id: 99 }),
      ra({ scope: "/sub/x", principal_id: "p-2" }),
    ];
    const groups = groupByScope(rows);
    expect(groups).toHaveLength(1);
    expect(groups[0].principals.map((p) => p.principal_id)).toEqual([
      "p-1",
      "p-2",
    ]);
  });

  test("picks the latest scan per scope", () => {
    const rows = [
      ra({
        scope: "/sub/x",
        scan_id: 1,
        scan_finished_at: "2026-05-01T00:00:00Z",
        principal_id: "p-1",
      }),
      ra({
        scope: "/sub/x",
        scan_id: 9,
        scan_finished_at: "2026-05-12T00:00:00Z",
        principal_id: "p-2",
      }),
    ];
    const [g] = groupByScope(rows);
    expect(g.latest_scan_id).toBe(9);
    expect(g.latest_scan_finished_at).toBe("2026-05-12T00:00:00Z");
  });

  test("orders buckets by latest-scan first", () => {
    const rows = [
      ra({
        scope: "/sub/a",
        scan_finished_at: "2026-05-01T00:00:00Z",
        principal_id: "p-1",
      }),
      ra({
        scope: "/sub/b",
        scan_finished_at: "2026-05-12T00:00:00Z",
        principal_id: "p-2",
      }),
    ];
    expect(groupByScope(rows).map((g) => g.scope)).toEqual(["/sub/b", "/sub/a"]);
  });

  test("falls back to scope ordering when finished_at ties", () => {
    const rows = [
      ra({ scope: "/sub/b", scan_finished_at: null, principal_id: "p-1" }),
      ra({ scope: "/sub/a", scan_finished_at: null, principal_id: "p-2" }),
    ];
    expect(groupByScope(rows).map((g) => g.scope)).toEqual(["/sub/a", "/sub/b"]);
  });

  test("any non-null timestamp beats null", () => {
    const rows = [
      ra({ scope: "/sub/a", scan_finished_at: null, principal_id: "p-1" }),
      ra({
        scope: "/sub/b",
        scan_finished_at: "2026-05-01T00:00:00Z",
        principal_id: "p-2",
      }),
    ];
    expect(groupByScope(rows).map((g) => g.scope)).toEqual(["/sub/b", "/sub/a"]);
  });
});
