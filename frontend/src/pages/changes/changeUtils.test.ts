import { describe, expect, it } from "vitest";

import type { AccessChange } from "@/api/client";

import { byDay, changeSentence, isGiven, sourceText, timeText } from "./changeUtils";

function change(over: Partial<AccessChange> = {}): AccessChange {
  return {
    id: 1,
    occurred_at: "2026-11-03T14:12:00Z",
    kind: "granted",
    source: "activity_log",
    scope: "/subscriptions/s1",
    scope_name: "prod",
    scope_level: "subscription",
    principal_id: "tom",
    principal_name: "Tom Becker",
    principal_type: "User",
    role_definition_id: "r",
    role_name: "Owner",
    level: "access",
    via_group_id: null,
    via_group_name: null,
    actor_id: "sp",
    actor_name: "sp-terraform",
    actor_type: null,
    direct_group_name: null,
    grant_id: "g1",
    check_id: 4,
    ...over,
  };
}

describe("change words", () => {
  it("says what happened", () => {
    expect(changeSentence(change())).toBe("Tom Becker was given Owner on prod");
    expect(changeSentence(change({ kind: "removed", role_name: "Reader" }))).toBe("Tom Becker lost Reader on prod");
    expect(changeSentence(change({ kind: "eligible_added" }))).toBe("Tom Becker can now switch on Owner on prod on request (PIM)");
    expect(
      changeSentence(change({ kind: "member_added", via_group_name: "Platform Admins", role_name: "Owner" })),
    ).toBe("Tom Becker joined Platform Admins, so they now have Owner on prod");
    expect(changeSentence(change({ principal_name: null, scope_name: null, scope: "/subscriptions/abc" }))).toBe(
      "tom was given Owner on abc",
    );
  });

  it("splits given from removed", () => {
    expect(isGiven("member_added")).toBe(true);
    expect(isGiven("eligible_removed")).toBe(false);
  });

  it("says how exact the time and author are", () => {
    expect(sourceText(change())).toBe("by sp-terraform · from the Azure Activity Log");
    expect(sourceText(change({ source: "created_on" }))).toBe("by sp-terraform · as recorded by Azure");
    expect(sourceText(change({ source: "daily_check", actor_id: null, actor_name: null }))).toMatch(/^first seen by the daily check/);
    expect(sourceText(change({ source: "daily_check", kind: "member_removed" }))).toMatch(/Entra ID audit log/);
    const joined = { kind: "member_added", source: "entra_audit", actor_name: "ada@example.org", via_group_name: "grp-readers" } as const;
    expect(sourceText(change(joined))).toBe("by ada@example.org · from the Entra ID audit log");
    expect(sourceText(change({ ...joined, actor_type: "app", actor_name: "MS-PIM", direct_group_name: "grp-nested" }))).toBe(
      "by the app MS-PIM · in grp-nested, inside grp-readers · from the Entra ID audit log",
    );
    expect(timeText(change(joined))).not.toMatch(/^~/);
    expect(sourceText(change({ source: "created_on", scope: "/providers/microsoft.aadiam" }))).toBe(
      "by sp-terraform · as recorded by Entra",
    );
    expect(timeText(change({ source: "daily_check" }))).toMatch(/^~/);
    expect(timeText(change())).not.toMatch(/^~/);
  });

  it("groups by day, newest first", () => {
    const days = byDay([
      change({ id: 3, occurred_at: "2026-11-03T15:00:00Z" }),
      change({ id: 2, occurred_at: "2026-11-03T10:00:00Z" }),
      change({ id: 1, occurred_at: "2026-11-01T10:00:00Z" }),
    ]);
    expect(days.map((d) => d.items.map((c) => c.id))).toEqual([[3, 2], [1]]);
  });
});

describe("owner changes", () => {
  it("say whose owner they became and what it lets them use", () => {
    expect(
      changeSentence(change({ kind: "owner_added", via_group_name: "sp-deploy", role_name: "Owner", principal_name: "ada" })),
    ).toMatch(/^ada became an owner of sp-deploy, so they can use its Owner on /);
    expect(
      changeSentence(change({ kind: "owner_removed", via_group_name: "grp-ops", role_name: "Reader", principal_name: "bob" })),
    ).toMatch(/^bob is no longer an owner of grp-ops, and can no longer use its Reader on /);
    expect(isGiven("owner_added")).toBe(true);
    expect(isGiven("owner_removed")).toBe(false);
    expect(sourceText(change({ source: "daily_check", kind: "owner_added" }))).toMatch(/who changed the owners/);
  });
});
