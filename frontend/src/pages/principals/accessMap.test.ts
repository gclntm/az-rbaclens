import { describe, expect, it } from "vitest";

import type { PrincipalAccessRow } from "@/api/client";

import { buildAccessMap, type PlaceMeta } from "./accessMap";

const ROOT = "/providers/microsoft.management/managementgroups/root";
const MG = "/providers/microsoft.management/managementgroups/mg-prod";
const SUB = "/subscriptions/s1";
const RG = "/subscriptions/s1/resourcegroups/rg-app";
const RES = `${RG}/providers/microsoft.storage/storageaccounts/st1`;
const OTHER = "/subscriptions/s2";

const places = new Map<string, PlaceMeta>([
  [ROOT, { name: "Tenant Root Group", level: "mgmt_group", parent: null }],
  [MG, { name: "mg-prod", level: "mgmt_group", parent: ROOT }],
  [SUB, { name: "prod", level: "subscription", parent: MG }],
  [RG, { name: "rg-app", level: "resource_group", parent: SUB }],
  [OTHER, { name: "dev", level: "subscription", parent: ROOT }],
]);

function row(scope: string, role: string, level: PrincipalAccessRow["level"], over: Partial<PrincipalAccessRow> = {}): PrincipalAccessRow {
  return {
    assignment_id: 1, scan_id: 1, scope, scope_name: scope.split("/").pop() ?? null, scope_level: "subscription",
    subscription_id: null, subscription_name: null, role_name: role, role_definition_id: role, role_type: null,
    role_risk: null, level, is_high_risk: false, source: "direct", via_group_id: null, via_group_name: null,
    observed_op_count: null, coverage_pct: null, open_findings: 0, ...over,
  };
}

describe("buildAccessMap", () => {
  it("lays grants on their places with the places above, depth first", () => {
    const map = buildAccessMap([row(SUB, "Owner", "access"), row(OTHER, "Reader", "view")], places);
    expect(map.map((r) => [r.name, r.depth, r.grants.map((g) => g.role_name)])).toEqual([
      ["Tenant Root Group", 0, []],
      ["mg-prod", 1, []],
      ["prod", 2, ["Owner"]],
      ["dev", 1, ["Reader"]],
    ]);
  });

  it("runs the rail from powerful access down, not for the rest", () => {
    const map = buildAccessMap(
      [row(MG, "Contributor", "keys"), row(RG, "Reader", "view"), row(OTHER, "Reader", "view")],
      places,
    );
    const by = Object.fromEntries(map.map((r) => [r.name, r]));
    expect([by["Tenant Root Group"].rail, by["mg-prod"].rail, by["prod"].rail, by["rg-app"].rail, by["dev"].rail]).toEqual([
      null, "keys", "keys", "keys", null,
    ]);
    expect(by["mg-prod"].railStarts).toBe(true);
    expect(by["prod"].railStarts).toBe(false);
    expect(by["prod"].fromAbove?.grant.role_name).toBe("Contributor");
    expect(by["prod"].fromAbove?.place).toBe("mg-prod");
  });

  it("a stronger rail lower down takes over", () => {
    const map = buildAccessMap([row(MG, "Contributor", "keys"), row(SUB, "Owner", "access")], places);
    const prod = map.find((r) => r.name === "prod")!;
    expect([prod.rail, prod.railStarts]).toEqual(["access", true]);
  });

  it("hangs single resources under their resource group", () => {
    const map = buildAccessMap([row(RES, "Storage Blob Data Reader", "data", { scope_level: "resource", scope_name: "st1" })], places);
    expect(map.map((r) => r.name)).toEqual(["Tenant Root Group", "mg-prod", "prod", "rg-app", "st1"]);
    expect(map[4].depth).toBe(4);
  });
});
