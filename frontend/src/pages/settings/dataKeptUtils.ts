/**
 * Settings › Data kept: the lines of the page and their wording. Pure — unit-tested in
 * dataKeptUtils.test.ts.
 */

import type { DataKept } from "@/api/client";

/** One line: the setting(s) it edits and the stored-now key it shows. */
export type DataLine = {
  key: string;
  title: string;
  what: string;
  /** Key of ``DataKept.stored``; null when the line has nothing stored of its own. */
  stored: string | null;
  storedNoun: string;
};

export const LINES: DataLine[] = [
  {
    key: "checks",
    title: "Checks",
    what: "Photos of who has which access, one per check: grants, eligibility, group members, findings.",
    stored: "checks",
    storedNoun: "checks",
  },
  {
    key: "failed_snapshot_retention_days",
    title: "Failed checks",
    what: "Checks that stopped with an error, kept to see why.",
    stored: "failed_checks",
    storedNoun: "checks",
  },
  {
    key: "event_retention_days",
    title: "Activity Log",
    what: "Who did what in Azure. Azure itself keeps 90 days.",
    stored: "activity_events",
    storedNoun: "events",
  },
  {
    key: "compact_after_days",
    title: "App activity in full detail",
    what: "Routine actions by apps, one line each; then one line per day with a count. People's actions, access changes and failures are always kept in full.",
    stored: null,
    storedNoun: "",
  },
  {
    key: "entra_audit_retention_days",
    title: "Entra ID audit log",
    what: "Group members, Entra roles and PIM activations. Entra keeps 30 days.",
    stored: "entra_audit",
    storedNoun: "entries",
  },
  {
    key: "change_retention_days",
    title: "Changes page",
    what: "Who got or lost access, and who did it.",
    stored: "changes",
    storedNoun: "changes",
  },
  {
    key: "audit_retention_days",
    title: "Audit log",
    what: "Every change made in RBAC Lens itself: decisions, settings.",
    stored: "audit",
    storedNoun: "entries",
  },
  {
    key: "pull_record_retention_days",
    title: "Collection runs",
    what: "The record of each collection: window, time, result. The events stay.",
    stored: "pulls",
    storedNoun: "runs",
  },
];

/** The preview's keys, in words. */
export const PREVIEW_WORDS: Record<string, string> = {
  checks: "checks",
  failed_checks: "failed checks",
  activity_events: "Activity Log events",
  entra_audit: "Entra ID audit log entries",
  changes: "changes",
  audit: "audit log entries",
  pulls: "collection runs",
  days_to_fold: "days of app activity folded",
};

/** Values that differ from what the page loaded; a value back at the server's is cleared. */
export function changedValues(data: DataKept, edited: Record<string, number>): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const [name, value] of Object.entries(edited)) {
    if (value === data.values[name]) continue;
    out[name] = value === data.server[name] ? null : value;
  }
  return out;
}

/** Is ``value`` acceptable for ``name``? */
export function isValid(data: DataKept, name: string, value: number): boolean {
  const [low, high] = data.bounds[name] ?? [0, 3650];
  return Number.isInteger(value) && ((value >= low && value <= high) || (value === 0 && data.never.includes(name)));
}

export function formatBytes(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = value / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

/** "About 365 checks, around 2.4 GB" for keeping every check ``days`` days; null when unknown. */
export function checksEstimate(data: DataKept, days: number): { text: string; large: boolean } | null {
  if (days <= 0 || data.checks_per_day <= 0) return null;
  const count = Math.round(days * data.checks_per_day);
  const size = data.bytes_per_check ? `, around ${formatBytes(count * data.bytes_per_check)}` : "";
  return { text: `About ${count.toLocaleString()} checks${size}.`, large: days > 90 };
}

/** "120 days", or "kept forever" for 0. */
export function daysText(days: number): string {
  return days === 0 ? "kept forever" : `${days} ${days === 1 ? "day" : "days"}`;
}
