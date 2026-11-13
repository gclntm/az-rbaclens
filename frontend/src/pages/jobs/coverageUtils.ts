import { type Coverage, type Freshness, type JobSchedule, MGMT_GROUPS, type SubscriptionCoverage } from "@/api/client";
import type { Tone } from "@/components/PageTemplate";

/** "18 min", "5 h", "12 days": how far behind, coarse. */
export function lagText(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.floor(hours / 24)} days`;
}

export function coverageTone(c: Pick<SubscriptionCoverage, "state">): Tone {
  if (c.state === "ok") return "ok";
  return c.state === "at_risk" || c.state === "lost" ? "danger" : "warn";
}

/** The middle of a coverage row: up to when, or how far behind and why. */
export function coverageText(c: SubscriptionCoverage): string {
  if (c.state === "no_schedule") return "not collected: no schedule";
  const why = !c.enabled ? " · paused" : c.collecting ? " · collecting now" : "";
  if (c.behind_hours === null) return `never collected${why}`;
  if (c.state === "ok") return `up to ${lagText(c.behind_hours)} ago`;
  return `${lagText(c.behind_hours)} behind${why}`;
}

/** The pill of a coverage row: the days left before Azure forgets, when that matters. */
export function coveragePill(c: SubscriptionCoverage): string {
  switch (c.state) {
    case "ok":
      return "Up to date";
    case "lost":
      return "Events lost";
    case "never":
      return "Not yet";
    case "no_schedule":
      return "No schedule";
    default:
      return `${c.days_left} ${c.days_left === 1 ? "day" : "days"} left`;
  }
}

/** The bar over the days Azure keeps: % collected (green) and % missing at the recent end. */
export function coverageBar(c: SubscriptionCoverage, keepsDays: number): { collected: number; missing: number } {
  if (c.behind_hours === null) return { collected: 0, missing: 0 };
  if (c.state === "ok") return { collected: 100, missing: 0 };
  // At least a sliver, so a few hours behind is still visible.
  const missing = Math.min(100, Math.max(2, Math.round((c.behind_hours / 24 / keepsDays) * 100)));
  return { collected: 100 - missing, missing };
}

export type TileContent = { tone: Tone; value: string; note: string };

/** "Who changed what": is every subscription's Activity Log collected? */
export function collectionTile(cov: Coverage | null): TileContent {
  if (!cov) return { tone: "info", value: "…", note: "" };
  const all = cov.subscriptions;
  if (all.length === 0) return { tone: "warn", value: "Nothing collected", note: "Set up after the first check" };
  const bad = all.filter((c) => c.state !== "ok");
  if (bad.length === 0) {
    const subs = all.filter((c) => c.subscription_id !== MGMT_GROUPS).length;
    const note =
      subs < all.length
        ? `All ${subs} subscriptions and the management groups collected`
        : `All ${subs} subscriptions collected`;
    return { tone: "ok", value: "Up to date", note };
  }
  const worst = bad[0]; // the API lists the worst first
  const late = bad.every((c) => c.behind_hours !== null);
  const detail =
    worst.behind_hours === null
      ? coverageText(worst)
      : `${lagText(worst.behind_hours)} behind${worst.days_left !== null && worst.days_left >= 0 && worst.behind_hours >= 24 ? `, ${worst.days_left} days left` : ""}`;
  return {
    tone: bad.some((c) => coverageTone(c) === "danger") ? "danger" : "warn",
    value: `${bad.length} of ${all.length} ${late ? "behind" : "need attention"}`,
    note: `${worst.name}: ${detail}`,
  };
}

/** "Who has which role": is the daily check doing its job? */
export function checkTile(daily: JobSchedule | null, fresh: Freshness | null, when: (iso: string) => string): TileContent {
  if (!fresh) return { tone: "info", value: "…", note: "" };
  const checked = fresh.access_as_of ? `Checked ${when(fresh.access_as_of)}` : "Not checked yet";
  if (!daily) return { tone: "warn", value: "No daily check", note: checked };
  if (!daily.enabled) return { tone: "warn", value: "Paused", note: `${checked} · not being refreshed` };
  if (fresh.last_check?.status === "failed" || (daily.failures ?? 0) > 0) {
    return { tone: "danger", value: "Last check failed", note: `${checked} · trying again` };
  }
  if ((fresh.warnings ?? []).some((w) => w.kind === "check_stale")) {
    return { tone: "warn", value: "Out of date", note: checked };
  }
  return { tone: "ok", value: "Up to date", note: checked };
}

/** "Schedules": how many run, how many are paused or failing. */
export function schedulesTile(counts: { total: number; on: number; off: number; failing: number }): TileContent {
  if (counts.total === 0) return { tone: "warn", value: "None yet", note: "Set up after the first check" };
  return {
    tone: counts.failing > 0 ? "danger" : counts.off > 0 ? "warn" : "info",
    value: counts.off > 0 ? `${counts.on} on, ${counts.off} paused` : `${counts.on} on`,
    note: counts.failing > 0 ? `${counts.failing} failed last time, trying again` : "None failing",
  };
}

/** The second pass in one line. */
export function secondPassText(cov: Coverage, when: (iso: string) => string): string {
  if (cov.second_pass_hours <= 0) return "Second pass: off";
  if (!cov.second_pass_finished_at) return "Second pass: not run yet (every night)";
  const n = cov.second_pass_late_events;
  const failed = cov.second_pass_failed > 0 ? ` · ${cov.second_pass_failed} failed` : "";
  return `Second pass ${when(cov.second_pass_finished_at)}: ${n.toLocaleString()} late ${n === 1 ? "event" : "events"}${failed}`;
}
