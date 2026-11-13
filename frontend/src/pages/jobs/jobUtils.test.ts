import { describe, expect, it } from "vitest";

import type { ScopeNode, SubscriptionNode } from "@/api/client";

import { everyLabel, jobHref, relativeTime, scheduleLabel, scopeOptions, targetDisplay, targetHref } from "./jobUtils";

const SUB = "8a3f1234-1111-2222-3333-aabbccddeeff";
const names = new Map([[SUB, "Prod"]]);

describe("everyLabel", () => {
  it("names presets and falls back to hours or days", () => {
    expect(everyLabel(24)).toBe("Daily");
    expect(everyLabel(168)).toBe("Weekly");
    expect(everyLabel(48)).toBe("Every 2 days");
    expect(everyLabel(3)).toBe("Every 3 hours");
    expect(scheduleLabel({ every_hours: 24, at_hour: 6 })).toBe("Daily at 06:00");
    expect(scheduleLabel({ every_hours: 1, at_hour: null })).toBe("Hourly");
  });
});

describe("relativeTime", () => {
  const now = new Date("2026-10-22T12:00:00Z");
  it("formats past and future coarsely", () => {
    expect(relativeTime("2026-10-22T11:48:00Z", now)).toBe("12 min ago");
    expect(relativeTime("2026-10-22T15:00:00Z", now)).toBe("in 3 h");
    expect(relativeTime("2026-10-19T12:00:00Z", now)).toBe("3 days ago");
    expect(relativeTime("2026-10-22T12:00:10Z", now)).toBe("just now");
    expect(relativeTime(null, now)).toBe("—");
  });
});

describe("targetDisplay", () => {
  it("names pulled subscriptions when known", () => {
    expect(targetDisplay("activity_pull", SUB.toUpperCase(), names)).toEqual({
      primary: "Prod",
      secondary: SUB.toUpperCase(),
    });
    expect(targetDisplay("activity_pull", "other", names)).toEqual({
      primary: "other",
      secondary: null,
    });
  });

  it("names the collection of the management groups' logs", () => {
    expect(targetDisplay("activity_pull", "management-groups", names)).toEqual({
      primary: "Management groups",
      secondary: null,
    });
    expect(targetHref("activity_pull", "management-groups")).toBe("/events?sub=management-groups");
  });

  it("uses the subscription name or last segment for snapshots", () => {
    expect(targetDisplay("snapshot", `/subscriptions/${SUB}`, names).primary).toBe("Prod");
    const mg = "/providers/microsoft.management/managementgroups/mg-platform";
    expect(targetDisplay("snapshot", mg, names)).toEqual({ primary: "mg-platform", secondary: mg });
  });
});

describe("links", () => {
  it("routes snapshots to the snapshot / scope and pulls to events", () => {
    expect(jobHref({ kind: "snapshot", id: 7, target: "/subscriptions/x" })).toBe("/iam-access/7");
    expect(jobHref({ kind: "activity_pull", id: 3, target: SUB })).toBe(`/events?sub=${SUB}`);
    expect(targetHref("snapshot", `/subscriptions/${SUB}`)).toBe(`/places/subscriptions/${SUB}`);
  });
});

describe("scopeOptions", () => {
  const node = (id: string, name: string, level: ScopeNode["level"], children: ScopeNode[] = []) =>
    ({ id, name, level, children }) as unknown as ScopeNode;
  const MG = "/providers/microsoft.management/managementgroups/mg-prod";
  const tree = [
    node(MG, "mg-prod", "mgmt_group", [
      node(`/subscriptions/${SUB}`, "prod", "subscription", [
        node(`/subscriptions/${SUB}/resourcegroups/rg-app`, "prod / rg-app", "resource_group"),
      ]),
    ]),
  ];
  const sub = (id: string, display_name: string) =>
    ({ id, display_name, scope: `/subscriptions/${id}`, mgmt_group_id: null }) as SubscriptionNode;

  it("lists management groups and subscriptions, indented, then subscriptions not checked yet", () => {
    const opts = scopeOptions(tree, [sub(SUB.toUpperCase(), "Prod"), sub("new-sub", "Sandbox")]);
    expect(opts).toEqual([
      { value: MG, label: "MG · mg-prod" },
      { value: `/subscriptions/${SUB}`, label: "\u00a0\u00a0Sub · prod" },
      { value: "/subscriptions/new-sub", label: "Sub · Sandbox (not checked yet)" },
    ]);
  });
});
