import { describe, expect, it } from "vitest";

import type { CapabilityGrant, CapabilityIdentity, PimSummary, ScopeCapabilities } from "@/api/client";

import {
  conditionNote,
  foldApps,
  pimSentence,
  howText,
  levelCounts,
  matchesFilter,
  powerText,
  statusTag,
  summarySentence,
} from "./whoUtils";

function grant(over: Partial<CapabilityGrant> = {}): CapabilityGrant {
  return {
    assignment_id: 1,
    role_name: "Owner",
    role_definition_id: "r",
    level: "access",
    how: "here",
    from_scope_id: "/subscriptions/s1",
    from_scope_name: "prod",
    from_scope_level: "subscription",
    ...over,
  };
}

function identity(over: Partial<CapabilityIdentity> = {}): CapabilityIdentity {
  return {
    id: "id-1",
    name: "alice@example.com",
    principal_type: "User",
    kind: "person",
    level: "access",
    data: false,
    data_write: false,
    keys_for: [],
    grants: [grant()],
    member_count: null,
    last_activity_at: null,
    eligible_grants: [],
    on_request: false,
    ...over,
  };
}

const NOW = new Date("2026-10-23T12:00:00Z");

describe("howText", () => {
  it("says where the strongest grant comes from, and how many more there are", () => {
    expect(howText(identity())).toBe("Owner · assigned here");
    expect(
      howText(identity({ grants: [grant({ role_name: "Reader", how: "inherited", from_scope_name: "mg-prod" }), grant(), grant()] })),
    ).toBe("Reader · inherited from mg-prod · +2 more roles");
  });
});

describe("PIM", () => {
  const eligibleOwner = grant({ eligible: true, assignment_id: null, until: "2027-03-01T00:00:00Z" });
  it("describes the eligible grant when the level is only on request", () => {
    const i = identity({ on_request: true, grants: [grant({ role_name: "Reader", level: "view" })], eligible_grants: [eligibleOwner] });
    expect(howText(i)).toMatch(/^Owner · eligible here · until .*2027 · \+1 more role$/);
    expect(statusTag(i, NOW)).toEqual({ text: "on request", tone: "warn" });
  });
  it("marks activated and time-bound active grants", () => {
    const i = identity({ grants: [grant({ activated: true, until: "2027-03-01T00:00:00Z", how: "inherited", from_scope_name: "mg-prod" })] });
    expect(howText(i)).toMatch(/^Owner · inherited from mg-prod · activated through PIM · until .*2027$/);
  });
  it("says what PIM found, including that it wasn't checked", () => {
    const base: PimSummary = { status: "read", error: null, unchecked: [], eligible_identities: 0, time_bound_grants: 0, permanent_privileged: 8 };
    expect(pimSentence(base)?.text).toBe("Nobody is eligible through PIM here. 8 identities hold access or keys permanently.");
    expect(pimSentence({ ...base, eligible_identities: 2 })?.text).toBe("2 identities can activate more access on request through PIM.");
    expect(pimSentence({ ...base, status: "not_checked" })?.text).toMatch(/these checks predate PIM support/);
    expect(
      pimSentence({ ...base, status: "not_checked", unchecked: [{ id: 21, scope_id: "/providers/x/managementgroups/mg-contoso", name: "mg-contoso" }] })?.text,
    ).toBe("Access on request (PIM) isn't fully known: mg-contoso (#21) predates PIM support. The next daily check fills it in.");
    expect(pimSentence({ ...base, status: "failed" })?.tone).toBe("warn");
    expect(pimSentence({ ...base, permanent_privileged: 0, time_bound_grants: 1 })?.text).toBe("Nobody is eligible through PIM here. 1 grant is time-bound.");
  });
});

describe("conditionNote", () => {
  it("speaks only for the grant that decides the level", () => {
    expect(conditionNote(identity())).toBeNull();
    const limited = grant({ condition_kind: "delegation", condition_text: "Can grant any role except Owner" });
    expect(conditionNote(identity({ grants: [limited] }))).toEqual({ text: "Can grant any role except Owner", unknown: false });
    expect(conditionNote(identity({ grants: [grant(), limited] }))).toBeNull();
    const odd = grant({ condition_kind: "other", condition_text: "Limited by a condition, check it" });
    expect(conditionNote(identity({ grants: [odd] }))?.unknown).toBe(true);
  });
});

describe("powerText", () => {
  it("explains the level for this identity", () => {
    expect(powerText(identity())).toBeNull(); // the section header already says it
    expect(powerText(identity({ level: "keys", keys_for: ["Key Vault secrets", "storage account keys", "Redis keys", "AI service keys"] }))).toBe(
      "Can fetch Key Vault secrets, storage account keys and 2 more",
    );
    expect(powerText(identity({ level: "data", data_write: false }))).toBe("Can read data");
    expect(powerText(identity({ level: "view" }))).toBeNull();
  });
});

describe("statusTag", () => {
  it("speaks about groups by membership and others by activity", () => {
    expect(statusTag(identity({ kind: "group", member_count: null }), NOW)).toEqual({ text: "members unknown", tone: "warn" });
    expect(statusTag(identity({ kind: "group", member_count: 0 }), NOW)?.text).toBe("empty group");
    expect(statusTag(identity({ kind: "group", member_count: 4 }), NOW)).toBeNull();
    expect(statusTag(identity(), NOW)?.text).toBe("no change recorded");
    expect(statusTag(identity({ last_activity_at: "2026-10-23T08:00:00Z" }), NOW)).toEqual({ text: "changed today", tone: "good" });
    expect(statusTag(identity({ last_activity_at: "2026-10-20T08:00:00Z" }), NOW)).toEqual({ text: "changed 3 d ago", tone: "good" });
    expect(statusTag(identity({ last_activity_at: "2026-05-01T08:00:00Z" }), NOW)?.text).toBe("no change in 90 days");
  });

  it("says nothing about usage on Entra ID, and names a partner's group", () => {
    expect(statusTag(identity(), NOW, true)).toBeNull();
    expect(statusTag(identity({ last_activity_at: "2026-10-23T08:00:00Z" }), NOW, true)).toBeNull();
    expect(statusTag(identity({ kind: "group", principal_type: "ForeignGroup", member_count: null }), NOW, true)).toEqual({
      text: "another tenant",
      tone: "warn",
    });
  });
});

describe("summarySentence and levelCounts", () => {
  const data = { identities: 30, people: 3, groups: 6, apps: 21, people_via_groups: 27 } as ScopeCapabilities;
  it("reads as a sentence", () => {
    expect(summarySentence(data, "prod")).toBe("30 identities can act on prod: 3 people, 6 groups (27 people in them) and 21 apps.");
    expect(summarySentence({ ...data, identities: 1, people: 1, groups: 0, apps: 0 }, "rg")).toBe(
      "1 identity can act on rg: 1 person and 0 apps.",
    );
  });
  it("counts a level, or says nobody", () => {
    expect(levelCounts({ level: "access", identities: [], people: 1, groups: 2, apps: 5, on_request: 0 })).toBe("1 person · 2 groups · 5 apps");
    expect(levelCounts({ level: "data", identities: [], people: 0, groups: 0, apps: 0, on_request: 0 })).toBe("nobody");
  });
});

describe("matchesFilter and foldApps", () => {
  const app = identity({ id: "a", name: "Defender CSPM", kind: "app" });
  it("filters by kind and by name, id or role", () => {
    expect(matchesFilter(app, "app", "")).toBe(true);
    expect(matchesFilter(app, "person", "")).toBe(false);
    expect(matchesFilter(identity(), "all", "OWNER")).toBe(true);
    expect(matchesFilter(identity(), "all", "nobody")).toBe(false);
  });
  it("folds apps only when there are more than a few", () => {
    const person = identity();
    const apps = ["a", "b", "c", "d"].map((id) => identity({ id, kind: "app" }));
    expect(foldApps([person, ...apps.slice(0, 3)]).folded).toEqual([]);
    const { shown, folded } = foldApps([person, ...apps]);
    expect(shown).toEqual([person]);
    expect(folded.map((i) => i.id)).toEqual(["a", "b", "c", "d"]);
  });
});
