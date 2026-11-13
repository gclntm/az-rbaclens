import { describe, expect, it } from "vitest";

import type { ReportGrant } from "@/api/client";

import { deltaText, deltaTrend, groupGrants, placesText, privilegedSummary, rowsToPrint, shownText } from "./reportPrintUtils";

function g(over: Partial<ReportGrant>): ReportGrant {
  return {
    grant_id: Math.random().toString(36), principal_id: "app-1", principal_name: "Defender", kind: "app",
    role_name: "User Access Administrator", level: "access", scope: "/subscriptions/1", scope_name: "infra",
    scope_level: "subscription", how: "standing", advice_rule: "R09 app-owner", advice_title: "Replace",
    advice_weight: "should", ...over,
  };
}
const place = (x: ReportGrant) => x.scope_name ?? x.scope;

describe("groupGrants", () => {
  it("puts the same identity and role on several places in one row", () => {
    const rows = groupGrants(
      [g({ scope_name: "infra" }), g({ scope_name: "test" }), g({ role_name: "Owner", scope_name: "prod" }), g({ how: "pim", scope_name: "dev" })],
      place,
    );
    expect(rows.map((r) => [r.role, r.pim, r.where])).toEqual([
      ["User Access Administrator", false, ["infra", "test"]],
      ["Owner", false, ["prod"]],
      ["User Access Administrator", true, ["dev"]],
    ]);
  });
});

describe("rows and counts", () => {
  it("shortens long place lists", () => {
    expect(placesText(["a", "b", "c"])).toBe("a, b, c");
    expect(placesText(["a", "b", "c", "d", "e", "f"])).toBe("a, b, c, +3");
  });

  it("prints every must-do row and the first should-do ones", () => {
    const rows = groupGrants(Array.from({ length: 10 }, (_, i) => g({ principal_id: `p${i}` })), place);
    expect(rowsToPrint(rows, "must")).toHaveLength(10);
    expect(rowsToPrint(rows, "should")).toHaveLength(8);
    expect(shownText(rowsToPrint(rows, "should"), 30)).toBe("8 of 30 shown, all in the CSV annex");
    expect(shownText(rows, 10)).toBe("all 10 shown");
  });

  it("sums the privileged list by what it allows and where", () => {
    const s = privilegedSummary([
      g({ level: "access", scope_level: "mgmt_group", advice_weight: "must" }),
      g({ level: "keys", scope_level: "subscription", advice_weight: "info" }),
      g({ level: "keys", scope_level: "subscription", advice_weight: "should" }),
    ]);
    expect(s).toEqual({ access: { mg: 1, sub: 0, action: 1 }, keys: { mg: 0, sub: 2, action: 1 }, total: 3, fine: 1 });
  });

  it("says how a lower-is-better number moved", () => {
    expect(deltaText(21, 21)).toBe("= baseline");
    expect([deltaText(23, 21), deltaTrend(23, 21)]).toEqual(["+2 since baseline", "worse"]);
    expect([deltaText(18, 21), deltaTrend(18, 21)]).toEqual(["−3 since baseline", "better"]);
    expect(deltaText(5, undefined)).toBe("");
  });
});
