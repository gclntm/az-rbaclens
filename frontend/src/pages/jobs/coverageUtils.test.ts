import { describe, expect, it } from "vitest";

import type { Coverage, EntraAudit, Freshness, JobSchedule, SubscriptionCoverage } from "@/api/client";

import {
  checkTile,
  collectionTile,
  coverageBar,
  coveragePill,
  coverageText,
  coverageTone,
  entraAuditText,
  lagText,
  schedulesTile,
  secondPassText,
} from "./coverageUtils";

function sub(over: Partial<SubscriptionCoverage>): SubscriptionCoverage {
  return {
    subscription_id: "s", name: "prod", schedule_id: 1, enabled: true, collecting: false, covered_until: "x",
    behind_hours: 0.3, days_left: 88, state: "ok", second_pass: null, ...over,
  };
}
const cov = (subscriptions: SubscriptionCoverage[], over: Partial<Coverage> = {}): Coverage => ({
  azure_keeps_days: 90, at_risk_days_left: 30, subscriptions, second_pass_hours: 48, second_pass_finished_at: "t",
  second_pass_subscriptions: subscriptions.length, second_pass_late_events: 0, second_pass_failed: 0,
  entra_audit: { status: null, checked_at: null, message: null, schedule_id: null, enabled: false, collecting: false, covered_until: null }, ...over,
});
const behind = sub({ name: "test", state: "behind", enabled: false, behind_hours: 12 * 24, days_left: 77 });
const atRisk = sub({ name: "old", state: "at_risk", behind_hours: 70 * 24, days_left: 19 });

describe("coverage rows", () => {
  it("says how far behind, coarsely", () => {
    expect([lagText(0.3), lagText(5.2), lagText(12 * 24)]).toEqual(["18 min", "5 h", "12 days"]);
  });

  it("colours by state and counts the days left", () => {
    expect([sub({}), behind, atRisk].map(coverageTone)).toEqual(["ok", "warn", "danger"]);
    expect(coverageText(sub({}))).toBe("up to 18 min ago");
    expect(coverageText(behind)).toBe("12 days behind · paused");
    expect(coveragePill(behind)).toBe("77 days left");
    expect(coveragePill(sub({ state: "lost", days_left: -6 }))).toBe("Events lost");
    expect(coverageText(sub({ state: "never", behind_hours: null, collecting: true }))).toBe("never collected · collecting now");
    expect(coverageText(sub({ state: "no_schedule", schedule_id: null }))).toBe("not collected: no schedule");
  });

  it("draws what is missing at the recent end of Azure's 90 days", () => {
    expect(coverageBar(sub({}), 90)).toEqual({ collected: 100, missing: 0 });
    expect(coverageBar(behind, 90)).toEqual({ collected: 87, missing: 13 });
    expect(coverageBar(sub({ state: "behind", behind_hours: 5 }), 90)).toEqual({ collected: 98, missing: 2 }); // a sliver
    expect(coverageBar(sub({ state: "lost", behind_hours: 95 * 24 }), 90)).toEqual({ collected: 0, missing: 100 });
    expect(coverageBar(sub({ state: "never", behind_hours: null }), 90)).toEqual({ collected: 0, missing: 0 });
  });
});

describe("status tiles", () => {
  const daily = { enabled: true, failures: 0 } as JobSchedule;
  const fresh = { access_as_of: "t", last_check: { status: "done" }, warnings: [] } as unknown as Freshness;
  const when = () => "today 10:02";

  it("who changed what", () => {
    expect(collectionTile(cov([sub({}), sub({})]))).toEqual({
      tone: "ok", value: "Up to date", note: "All 2 subscriptions collected",
    });
    expect(collectionTile(cov([sub({}), sub({}), sub({ subscription_id: "management-groups" })])).note).toBe(
      "All 2 subscriptions and the management groups collected",
    );
    expect(collectionTile(cov([behind, sub({})]))).toEqual({
      tone: "warn", value: "1 of 2 behind", note: "test: 12 days behind, 77 days left",
    });
    expect(collectionTile(cov([atRisk, behind, sub({})])).tone).toBe("danger");
    expect(collectionTile(cov([sub({ state: "no_schedule", behind_hours: null }), sub({})])).value).toBe(
      "1 of 2 need attention",
    );
  });

  it("who has which role", () => {
    expect(checkTile(daily, fresh, when)).toEqual({ tone: "ok", value: "Up to date", note: "Checked today 10:02" });
    expect(checkTile({ ...daily, enabled: false }, fresh, when).value).toBe("Paused");
    expect(checkTile({ ...daily, failures: 1 }, fresh, when)).toMatchObject({ tone: "danger", value: "Last check failed" });
    expect(checkTile(daily, { ...fresh, warnings: [{ kind: "check_stale" }] } as never, when).value).toBe("Out of date");
    expect(checkTile(null, fresh, when).value).toBe("No daily check");
  });

  it("schedules", () => {
    expect(schedulesTile({ total: 9, on: 9, off: 0, failing: 0 })).toEqual({ tone: "info", value: "9 on", note: "None failing" });
    expect(schedulesTile({ total: 9, on: 8, off: 1, failing: 0 })).toMatchObject({ tone: "warn", value: "8 on, 1 paused" });
    expect(schedulesTile({ total: 9, on: 9, off: 0, failing: 2 })).toMatchObject({
      tone: "danger", note: "2 failed last time, trying again",
    });
  });

  it("the second pass", () => {
    const when2 = () => "today 03:12";
    expect(secondPassText(cov([]), when2)).toBe("Second pass today 03:12: 0 late events");
    expect(secondPassText(cov([], { second_pass_late_events: 1, second_pass_failed: 2 }), when2)).toBe(
      "Second pass today 03:12: 1 late event · 2 failed",
    );
    expect(secondPassText(cov([], { second_pass_finished_at: null }), when2)).toBe("Second pass: not run yet (every night)");
    expect(secondPassText(cov([], { second_pass_hours: 0 }), when2)).toBe("Second pass: off");
  });

  it("says whether the Entra ID audit log is read, and why not", () => {
    const e = (over: Partial<EntraAudit>): EntraAudit => ({ ...cov([]).entra_audit, ...over });
    const when = (iso: string) => `at ${iso}`;
    expect(entraAuditText(e({}), when).text).toMatch(/^Not tried yet/);
    expect(entraAuditText(e({ status: "no_permission" }), when).text).toMatch(/AuditLog\.Read\.All/);
    expect(entraAuditText(e({ status: "licence_required" }), when).text).toMatch(/P1 or P2/);
    const read = e({ status: "readable", schedule_id: 7, enabled: true, covered_until: "t" });
    expect(entraAuditText(read, when)).toEqual({
      text: "Collected every hour (who adds or removes group members), up to at t.",
      tone: "ok",
    });
    expect(entraAuditText({ ...read, enabled: false }, when).tone).toBe("warn");
  });
});
