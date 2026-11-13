import {
  type Job,
  type JobFilter,
  type JobKind,
  type JobSchedule,
  type JobScheduleUpdate,
  MGMT_GROUPS,
  MGMT_GROUPS_NAME,
  type ScopeNode,
  type SubscriptionNode,
} from "@/api/client";
import { LEVEL_SHORT, lastSegment, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";

export const KIND_LABEL: Record<JobKind, string> = {
  snapshot: "Check",
  activity_pull: "Activity Log collection",
};

export const EVERY_PRESETS = [
  { hours: 1, label: "Hourly" },
  { hours: 6, label: "Every 6 hours" },
  { hours: 12, label: "Every 12 hours" },
  { hours: 24, label: "Daily" },
  { hours: 168, label: "Weekly" },
] as const;

export function everyLabel(hours: number): string {
  const preset = EVERY_PRESETS.find((p) => p.hours === hours);
  if (preset) return preset.label;
  if (hours % 24 === 0) return `Every ${hours / 24} days`;
  return `Every ${hours} hours`;
}

/** "Daily at 06:00" for an hour-aligned schedule, else the interval label. */
export function scheduleLabel(s: { every_hours: number; at_hour?: number | null }): string {
  if (s.at_hour !== null && s.at_hour !== undefined) return `Daily at ${String(s.at_hour).padStart(2, "0")}:00`;
  return everyLabel(s.every_hours);
}

/** "in 3 h", "12 min ago", "just now" — coarse, for schedule columns. */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const diffMin = Math.round((new Date(iso).getTime() - now.getTime()) / 60_000);
  const abs = Math.abs(diffMin);
  if (abs < 1) return diffMin <= 0 ? "just now" : "within a minute";
  let amount: string;
  if (abs < 60) amount = `${abs} min`;
  else if (abs < 48 * 60) amount = `${Math.round(abs / 60)} h`;
  else amount = `${Math.round(abs / (24 * 60))} days`;
  return diffMin < 0 ? `${amount} ago` : `in ${amount}`;
}

const SUBSCRIPTION_ONLY = /^\/subscriptions\/([^/]+)$/i;

/**
 * What to show for a job or schedule target. Pulls target a
 * subscription GUID; snapshots an ARM scope, shown by its last segment
 * (or the subscription's name when the scope is a whole subscription).
 */
export function targetDisplay(
  kind: JobKind,
  target: string,
  subNames: ReadonlyMap<string, string>,
): { primary: string; secondary: string | null } {
  if (kind === "activity_pull") {
    if (target.toLowerCase() === MGMT_GROUPS) return { primary: MGMT_GROUPS_NAME, secondary: null };
    const name = subNames.get(target.toLowerCase());
    return name ? { primary: name, secondary: target } : { primary: target, secondary: null };
  }
  // A known place (see placeNames): its name is enough, the path stays a tooltip.
  const known = subNames.get(target.toLowerCase());
  if (known) return { primary: known, secondary: null };
  const sub = SUBSCRIPTION_ONLY.exec(target);
  const name = sub ? subNames.get(sub[1].toLowerCase()) : undefined;
  return { primary: name ?? lastSegment(target), secondary: target };
}

/**
 * Names for schedule and job targets: subscriptions by GUID (from Azure's list, else from the
 * places the checks found) and every management group or subscription by its ARM path.
 */
export function placeNames(subscriptions: SubscriptionNode[], tree: ScopeNode[]): Map<string, string> {
  const names = new Map<string, string>();
  const walk = (nodes: ScopeNode[]) => {
    for (const n of nodes) {
      if (n.level !== "mgmt_group" && n.level !== "subscription") continue;
      if (n.name) {
        names.set(n.id.toLowerCase(), n.name);
        const sub = SUBSCRIPTION_ONLY.exec(n.id);
        if (sub) names.set(sub[1].toLowerCase(), n.name);
      }
      walk(n.children);
    }
  };
  walk(tree);
  for (const s of subscriptions) names.set(s.id.toLowerCase(), s.display_name);
  return names;
}

/** Where a job row links: the snapshot, or the pulled subscription's events. */
export function jobHref(job: Pick<Job, "kind" | "id" | "target">): string {
  if (job.kind === "snapshot") return `/iam-access/${job.id}`;
  return `/events?sub=${encodeURIComponent(job.target)}`;
}

/** Where a schedule's target links: the scope page, or its events. */
export function targetHref(kind: JobKind, target: string): string {
  if (kind === "snapshot") return scopeHref(target);
  return `/events?sub=${encodeURIComponent(target)}`;
}

export function isActive(status: string | null | undefined): boolean {
  return status === "pending" || status === "running";
}

export type ScopeOption = { value: string; label: string };

/**
 * Places a check can start from: management groups and subscriptions of
 * the known tree, indented by depth, then subscriptions Azure lists that
 * no check has reached yet. Values are ARM paths (the API takes them).
 */
export function scopeOptions(tree: ScopeNode[], subscriptions: SubscriptionNode[]): ScopeOption[] {
  const out: ScopeOption[] = [];
  const seen = new Set<string>();
  const walk = (nodes: ScopeNode[], depth: number) => {
    for (const n of nodes) {
      if (n.level !== "mgmt_group" && n.level !== "subscription") continue;
      seen.add(n.id.toLowerCase());
      out.push({
        value: n.id,
        label: `${"\u00a0\u00a0".repeat(depth)}${LEVEL_SHORT[n.level]} · ${scopeLabel(n.id, n.name)}`,
      });
      walk(n.children, depth + 1);
    }
  };
  walk(tree, 0);
  const fresh = subscriptions
    .filter((s) => !seen.has(s.scope.toLowerCase()))
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
  for (const s of fresh) {
    out.push({ value: s.scope, label: `Sub · ${s.display_name} (not checked yet)` });
  }
  return out;
}

/** The daily check first, then every schedule by the name the user reads. */
export function orderSchedules(schedules: JobSchedule[], subNames: ReadonlyMap<string, string>): JobSchedule[] {
  const name = (s: JobSchedule) => targetDisplay(s.kind, s.target, subNames).primary.toLowerCase();
  const rank = (s: JobSchedule) => (s.kind === "snapshot" ? (s.at_hour != null ? 0 : 1) : 2);
  return [...schedules].sort((a, b) => rank(a) - rank(b) || name(a).localeCompare(name(b)));
}

/** How many schedules are on, paused, and failing (last run failed). */
export function scheduleCounts(schedules: JobSchedule[]): { total: number; on: number; off: number; failing: number } {
  const off = schedules.filter((s) => !s.enabled).length;
  const failing = schedules.filter((s) => s.enabled && (s.failures ?? 0) > 0).length;
  return { total: schedules.length, on: schedules.length - off, off, failing };
}

/** The question asked before pausing: what stops. */
export function pauseQuestion(kind: JobKind, name: string): string {
  return kind === "snapshot"
    ? `Pause the check of ${name}? Who has which role there stops being refreshed until you resume.`
    : `Pause collection for ${name}? Changes there stop being recorded until you resume.`;
}

/** How long a run took: "8 s", "1 min 39 s", "2 h 05 min"; "—" while it has no end. */
export function durationText(started: string | null, finished: string | null): string {
  if (!started || !finished) return "—";
  const s = Math.max(0, Math.round((new Date(finished).getTime() - new Date(started).getTime()) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

export type RunFilter = "all" | "checks" | "collections" | "failed";

export const RUN_FILTERS: { key: RunFilter; label: string; filter: JobFilter }[] = [
  { key: "all", label: "All", filter: {} },
  { key: "checks", label: "Checks", filter: { kind: "snapshot" } },
  { key: "collections", label: "Collections", filter: { kind: "activity_pull" } },
  { key: "failed", label: "Failed", filter: { status: "failed" } },
];

/** What an edited schedule sends: the hour only makes sense for a daily one (-1 clears it). */
export function editPatch(
  s: Pick<JobSchedule, "kind">,
  form: { everyHours: number; atHour: number | null; lookbackDays: number },
): JobScheduleUpdate {
  const patch: JobScheduleUpdate = {
    every_hours: form.everyHours,
    at_hour: form.everyHours === 24 && form.atHour !== null ? form.atHour : -1,
  };
  if (s.kind === "activity_pull") patch.initial_window_days = form.lookbackDays;
  return patch;
}

/** "since today 14:02" or "since today 14:02, by anna@example.com" for a paused schedule. */
export function pausedText(s: Pick<JobSchedule, "paused_at" | "paused_by">, when: (iso: string) => string): string {
  if (!s.paused_at) return "";
  return `since ${when(s.paused_at)}${s.paused_by ? `, by ${s.paused_by}` : ""}`;
}
