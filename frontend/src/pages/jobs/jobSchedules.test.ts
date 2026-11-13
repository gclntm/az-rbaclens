import { describe, expect, it } from "vitest";

import type { JobSchedule } from "@/api/client";

import {
  durationText,
  editPatch,
  orderSchedules,
  pausedText,
  pauseQuestion,
  placeNames,
  scheduleCounts,
  targetDisplay,
} from "./jobUtils";

function sched(over: Partial<JobSchedule>): JobSchedule {
  return {
    id: 1, kind: "activity_pull", target: "sub-a", every_hours: 1, at_hour: null, enabled: true,
    initial_window_days: 90, next_run_at: "", last_run_at: null, last_job_id: null, last_job_status: null,
    last_error: null, failures: 0, created_at: "", ...over,
  };
}
const names = new Map([["sub-a", "prod"], ["sub-b", "infra"]]);

describe("schedules on the Data & schedules page", () => {
  it("lists the daily check first, then by name", () => {
    const daily = sched({ id: 9, kind: "snapshot", target: "/providers/x/mg-root", every_hours: 24, at_hour: 6 });
    const got = orderSchedules([sched({ id: 1, target: "sub-a" }), daily, sched({ id: 2, target: "sub-b" })], names);
    expect(got.map((s) => s.id)).toEqual([9, 2, 1]); // daily check, infra, prod
  });

  it("counts on, paused and failing", () => {
    const all = [sched({}), sched({ enabled: false }), sched({ failures: 2 }), sched({ enabled: false, failures: 1 })];
    expect(scheduleCounts(all)).toEqual({ total: 4, on: 2, off: 2, failing: 1 });
  });

  it("says what stops before pausing", () => {
    expect(pauseQuestion("activity_pull", "prod")).toMatch(/Pause collection for prod\? Changes there stop being recorded/);
    expect(pauseQuestion("snapshot", "Tenant Root Group")).toMatch(/stops being refreshed/);
  });

  it("names a place by what the checks found, not by its id", () => {
    const root = "/providers/microsoft.management/managementgroups/1111";
    const sub = "/subscriptions/aaaaaaaa-0000-0000-0000-000000000001";
    const tree = [{ id: root, name: "Tenant Root Group", level: "mgmt_group",
      children: [{ id: sub, name: "prod", level: "subscription", children: [] }] }] as never;
    const known = placeNames([], tree);
    expect(targetDisplay("snapshot", root, known)).toEqual({ primary: "Tenant Root Group", secondary: null });
    expect(targetDisplay("activity_pull", "AAAAAAAA-0000-0000-0000-000000000001", known).primary).toBe("prod");
    expect(targetDisplay("snapshot", "/providers/microsoft.management/managementgroups/unknown", known).primary).toBe("unknown");
  });

  it("says how long a run took", () => {
    expect(durationText("2026-11-10T08:00:00Z", "2026-11-10T08:00:08Z")).toBe("8 s");
    expect(durationText("2026-11-10T08:00:00Z", "2026-11-10T08:01:39Z")).toBe("1 min 39 s");
    expect(durationText("2026-11-10T08:00:00Z", "2026-11-10T10:05:00Z")).toBe("2 h 05 min");
    expect(durationText("2026-11-10T08:00:00Z", null)).toBe("—");
  });

  it("sends an hour only for a daily schedule", () => {
    const form = { everyHours: 24, atHour: 6, lookbackDays: 30 };
    expect(editPatch({ kind: "snapshot" }, form)).toEqual({ every_hours: 24, at_hour: 6 });
    expect(editPatch({ kind: "snapshot" }, { ...form, everyHours: 12 })).toEqual({ every_hours: 12, at_hour: -1 });
    expect(editPatch({ kind: "snapshot" }, { ...form, atHour: null })).toEqual({ every_hours: 24, at_hour: -1 });
    expect(editPatch({ kind: "activity_pull" }, { ...form, everyHours: 1 })).toEqual({
      every_hours: 1, at_hour: -1, initial_window_days: 30,
    });
  });

  it("says since when, and by whom when known", () => {
    const when = () => "today 14:02";
    expect(pausedText({ paused_at: "x", paused_by: "anna" }, when)).toBe("since today 14:02, by anna");
    expect(pausedText({ paused_at: "x", paused_by: null }, when)).toBe("since today 14:02");
    expect(pausedText({ paused_at: null, paused_by: null }, when)).toBe("");
  });
});
