import { describe, expect, it } from "vitest";

import type { ReviewItem } from "@/api/client";

import {
  adviceSummary,
  bar,
  decided,
  decisionsFor,
  dueText,
  matches,
  nextDecision,
  periodLabel,
  removeCommands,
  roundTitle,
  subjectLine,
} from "./reviewUtils";

function item(over: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: 1, grant_id: "g", scope: "/subscriptions/s", scope_name: "prod", scope_level: "subscription",
    principal_id: "tom", principal_name: "Tom", principal_type: "User", role_definition_id: "r", role_name: "Owner",
    level: "access", moved_from_scope: null, moved_from_name: null, decision: null, note: null, decided_by_name: null,
    decided_at: null, carried: false, ask_remind_at: null, gone_at: null, remove_command: "az role assignment delete --ids x",
    ...over,
  };
}

describe("review words", () => {
  it("names months and rounds", () => {
    expect(periodLabel("2026-11")).toBe("November 2026");
    expect(roundTitle({ scope_id: "/subscriptions/s", scope_name: "prod", period: "2026-11" })).toBe("November review of prod");
    expect(roundTitle({ scope_id: null, scope_name: null, period: "2026-12" })).toBe("December review for the admins");
  });

  it("filters items, keeping removed-in-Azure apart", () => {
    expect(matches(item(), "open")).toBe(true);
    expect(matches(item({ decision: "keep" }), "keep")).toBe(true);
    expect(matches(item({ gone_at: "2026-11-05" }), "open")).toBe(false);
    expect(matches(item({ gone_at: "2026-11-05" }), "gone")).toBe(true);
    expect(matches(item({ gone_at: "2026-11-05", decision: "remove" }), "remove")).toBe(true);
  });

  it("toggles a decision and lists commands", () => {
    expect(nextDecision("keep", "keep")).toBeNull();
    expect(nextDecision(null, "ask")).toBe("ask");
    expect(removeCommands([item({ decision: "remove" }), item({ decision: "keep" })])).toBe(
      "# Tom · Owner on prod\naz role assignment delete --ids x",
    );
    // A line break in a name stays on the comment line.
    expect(removeCommands([item({ decision: "remove", principal_name: "Tom\nrm x" })])).toBe(
      "# Tom rm x · Owner on prod\naz role assignment delete --ids x",
    );
  });

  it("sizes the bar without counting gone grants", () => {
    const p = { total: 12, keep: 4, remove: 2, change: 1, ask: 1, open: 2, gone: 2 };
    expect(bar({ progress: p })).toEqual({ keep: 40, remove: 20, change: 10, ask: 10 });
    expect(decided({ progress: p })).toBe(8);
  });

  it("counts the advice still to accept, by suggested decision", () => {
    const items = [
      item({ suggested: "remove" }),
      item({ suggested: "keep" }),
      item({ suggested: "keep", decision: "remove" }), // decided: not counted
      item({ suggested: "change", gone_at: "2026-11-05" }), // gone: not counted
      item({ suggested: null }),
    ];
    expect(adviceSummary(items)).toEqual({ keep: 1, remove: 1, change: 0, ask: 0, total: 2 });
    expect(matches(item({ decision: "change" }), "change")).toBe(true);
  });

  it("says when it is due", () => {
    const now = new Date("2026-11-08T12:00:00Z");
    expect(dueText("2026-11-30T22:59:59Z", now)).toBe("due in 23 days");
    expect(dueText("2026-11-08T20:00:00Z", now)).toBe("due in 1 day");
    expect(dueText("2026-11-06T22:59:59Z", now)).toBe("2 days late");
  });
});

describe("owners and secrets", () => {
  it("say what an owner or secret item is about", () => {
    expect(
      subjectLine({
        kind: "owner",
        role_name: "Owner",
        detail: { object_name: "sp-deploy", object_kind: "app", via: ["registration", "service_principal"] },
      }),
    ).toBe("owns the app sp-deploy (its app registration and its service principal), which holds Owner");
    expect(
      subjectLine({ kind: "secret", role_name: "Owner", detail: { object_name: "sp-deploy", via: "registration", kind: "secret", end_at: "2020-01-01T00:00:00Z" } }),
    ).toMatch(/^Secret on the app registration of sp-deploy, expired .*; it holds Owner$/);
    expect(subjectLine({ kind: "grant", role_name: "Owner" })).toBeNull();
  });
  it("offer no Change for an owner", () => {
    expect(decisionsFor("owner")).toEqual(["keep", "remove", "ask"]);
    expect(decisionsFor("secret")).toContain("change");
  });
});
