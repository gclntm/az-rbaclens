import type { ControlStatus, ReportGrant, ReportNumbers } from "@/api/client";

export const STATUS_LABEL: Record<ControlStatus, string> = {
  met: "Met",
  partly: "Partly met",
  not_met: "Not met",
};

const STATUS_RANK: Record<ControlStatus, number> = { not_met: 0, partly: 1, met: 2 };

/** The key numbers a saved assessment keeps, in report order. All are "lower is better". */
export const NUMBER_LABEL: Record<string, string> = {
  standing_admin_high: "Standing grants that can change access, high up",
  standing_admin_guests: "… of them to guests",
  keys_high: "Standing keys and passwords access, high up",
  orphans: "Grants to identities that no longer exist",
  guests_with_power: "Guests with power",
  unknown_member_groups: "Groups whose members can't be read",
  least_privilege: "Grants larger than needed",
  must: "Grants with a must-do advice",
  should: "Grants with a should-do advice",
  subscriptions_without_responsible: "Subscriptions with nobody responsible",
  todos_overdue: "Overdue changes from reviews",
  activity_gap_hours: "Activity Log behind (hours)",
};

export type NumberChange = { key: string; label: string; before: number | null; now: number | null; better: boolean | null };

/** Key numbers now against a saved assessment; ``better`` is null when unchanged or unknown. */
export function compareNumbers(now: ReportNumbers, before: ReportNumbers): NumberChange[] {
  return Object.entries(NUMBER_LABEL).map(([key, label]) => {
    const a = before[key] ?? null;
    const b = now[key] ?? null;
    const better = a === null || b === null || a === b ? null : b < a;
    return { key, label, before: a, now: b, better };
  });
}

/** "better" | "worse" | "same" for a control's status against a saved one. */
export function statusTrend(now: ControlStatus, before: ControlStatus | undefined): "better" | "worse" | "same" | null {
  if (!before) return null;
  const d = STATUS_RANK[now] - STATUS_RANK[before];
  return d > 0 ? "better" : d < 0 ? "worse" : "same";
}

export const HOW_LABEL: Record<ReportGrant["how"], string> = {
  standing: "standing",
  pim: "PIM",
  "time-bound": "time-bound",
};

export function hoursText(h: number | null): string {
  if (h === null) return "—";
  if (h < 48) return `${Math.round(h)} h`;
  return `${Math.round(h / 24)} days`;
}

/** A CSS string literal: quotes and backslashes escaped, line breaks and angle brackets dropped. */
export function cssString(text: string): string {
  return `"${text.replace(/[\\"]/g, "\\$&").replace(/[\n\r<>]/g, " ")}"`;
}
