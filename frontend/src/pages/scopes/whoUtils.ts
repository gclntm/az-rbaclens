/**
 * Plain-language helpers for the scope page's "Who can do what" tab.
 * Pure — unit-tested in whoUtils.test.ts.
 */

import type {
  CapabilityGrant,
  CapabilityIdentity,
  CapabilityLevel,
  CapabilityLevelGroup,
  PimSummary,
  ScopeCapabilities,
} from "@/api/client";

export const LEVEL_INFO: Record<CapabilityLevel, { title: string; explain: string }> = {
  access: {
    title: "Can change who has access",
    explain: "Can give anyone, including themselves, any of the powers below.",
  },
  keys: {
    title: "Can get keys and passwords",
    explain:
      "Can fetch keys or credentials that unlock everything inside, and that keep working after their role is removed.",
  },
  change: {
    title: "Can change resources",
    explain: "Can create, reconfigure or delete things, but not fetch their keys.",
  },
  data: {
    title: "Can read or change data",
    explain: "Works on the contents of storage and services, not on the resources themselves.",
  },
  view: {
    title: "Can only look",
    explain: "Sees configuration, nothing inside.",
  },
  unknown: {
    title: "Unknown role, check it",
    explain: "These roles' definitions were never read, so what they allow is unknown.",
  },
};

export type KindFilter = "all" | "person" | "group" | "app";

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "1 Mar 2027" — PIM end dates are about days, not minutes. */
export function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** The grant the row is about: the eligible one when the level is reachable only on request. */
export function decidingGrant(identity: CapabilityIdentity): CapabilityGrant | undefined {
  return identity.on_request ? identity.eligible_grants[0] : identity.grants[0];
}

/**
 * "Owner · assigned here", "Reader · inherited from mg-prod · +1 more role",
 * "Owner · eligible here · until 1 Mar 2027", "Owner · assigned here · activated through PIM".
 */
export function howText(identity: CapabilityIdentity): string {
  const g = decidingGrant(identity);
  if (!g) return "";
  const role = g.role_name ?? "Unnamed role";
  const from = g.from_scope_name ?? g.from_scope_id;
  const where = g.eligible
    ? g.how === "here" ? "eligible here" : `eligible from ${from}`
    : g.how === "here" ? "assigned here" : `inherited from ${from}`;
  const pim = [g.activated ? "activated through PIM" : null, g.until ? `until ${formatDay(g.until)}` : null]
    .filter(Boolean)
    .join(" · ");
  const others = identity.grants.length + identity.eligible_grants.length - 1;
  const more = others > 0 ? ` · +${plural(others, "more role")}` : "";
  return `${role} · ${where}${pim ? ` · ${pim}` : ""}${more}`;
}

/** The one extra line that says what the level means for this identity, or null. */
export function powerText(identity: CapabilityIdentity): string | null {
  if (identity.level === "keys" && identity.keys_for.length) {
    const [a, b, ...more] = identity.keys_for;
    const list = [a, b].filter(Boolean).join(", ");
    return `Can fetch ${list}${more.length ? ` and ${more.length} more` : ""}`;
  }
  if (identity.level === "data") return identity.data_write ? "Can read and change data" : "Can read data";
  return null;
}

/**
 * The caveat when the grant that decides this identity's level carries a
 * condition — e.g. "Can grant any role except Owner, …". Conditions on
 * weaker grants don't change what the row claims, so they're left to the
 * Access tab.
 */
export function conditionNote(identity: CapabilityIdentity): { text: string; unknown: boolean } | null {
  const deciding = decidingGrant(identity);
  if (!deciding?.condition_kind || !deciding.condition_text) return null;
  return { text: deciding.condition_text, unknown: deciding.condition_kind === "other" };
}

export type StatusTag = { text: string; tone: "good" | "warn" | "muted" } | null;

const DAY = 86_400_000;

/** One tag per row, only when it's worth reading. */
export function statusTag(identity: CapabilityIdentity, now: Date = new Date()): StatusTag {
  if (identity.on_request) return { text: "on request", tone: "warn" };
  if (identity.kind === "group") {
    if (identity.member_count === null) return { text: "members unknown", tone: "warn" };
    if (identity.member_count === 0) return { text: "empty group", tone: "muted" };
    return null;
  }
  if (!identity.last_activity_at) return { text: "no change recorded", tone: "muted" };
  const days = Math.floor((now.getTime() - new Date(identity.last_activity_at).getTime()) / DAY);
  if (days > 90) return { text: "no change in 90 days", tone: "muted" };
  if (days <= 0) return { text: "changed today", tone: "good" };
  return { text: `changed ${days} d ago`, tone: days <= 30 ? "good" : "muted" };
}

/** "30 identities can act on prod: 3 people, 6 groups (27 people) and 21 apps." */
export function summarySentence(data: ScopeCapabilities, scopeName: string): string {
  const parts = [
    plural(data.people, "person", "people"),
    data.groups
      ? `${plural(data.groups, "group")}${data.people_via_groups ? ` (${plural(data.people_via_groups, "person", "people")} in them)` : ""}`
      : null,
    plural(data.apps, "app"),
  ].filter(Boolean) as string[];
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts[0];
  return `${plural(data.identities, "identity", "identities")} can act on ${scopeName}: ${list}.`;
}

/**
 * What PIM says here, in one sentence — or null when there's nothing to add.
 * "Not checked" is said out loud: silence would read as "nobody is eligible".
 */
export function pimSentence(pim: PimSummary): { text: string; tone: "muted" | "warn" } | null {
  if (pim.status === "not_checked") {
    const names = pim.unchecked.map((s) => `${s.name ?? s.scope_id.split("/").pop()} (#${s.id})`);
    const which = names.length
      ? `${names.slice(0, 3).join(", ")}${names.length > 3 ? ` and ${names.length - 3} more` : ""}`
      : "these checks";
    return {
      text: `Access on request (PIM) isn't fully known: ${which} ${names.length === 1 ? "predates" : "predate"} PIM support. The next daily check fills it in.`,
      tone: "muted",
    };
  }
  if (pim.status === "failed") {
    return { text: "PIM couldn't be read in the last check, so access on request isn't shown.", tone: "warn" };
  }
  const partial = pim.status === "partial" ? " PIM couldn't be read everywhere, so this may be incomplete." : "";
  if (pim.eligible_identities > 0) {
    return {
      text: `${plural(pim.eligible_identities, "identity", "identities")} can activate more access on request through PIM.${partial}`,
      tone: "muted",
    };
  }
  const timeBound = pim.time_bound_grants
    ? ` ${plural(pim.time_bound_grants, "grant")} ${pim.time_bound_grants === 1 ? "is" : "are"} time-bound.`
    : "";
  const standing = pim.permanent_privileged
    ? ` ${plural(pim.permanent_privileged, "identity", "identities")} hold${pim.permanent_privileged === 1 ? "s" : ""} access or keys permanently.`
    : "";
  return { text: `Nobody is eligible through PIM here.${standing}${timeBound}${partial}`, tone: "muted" };
}

export function matchesFilter(identity: CapabilityIdentity, kind: KindFilter, query: string): boolean {
  if (kind !== "all" && (kind === "app" ? !["app", "other"].includes(identity.kind) : identity.kind !== kind)) {
    return false;
  }
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [identity.name, identity.id, ...[...identity.grants, ...identity.eligible_grants].map((g) => g.role_name)].some((v) =>
    (v ?? "").toLowerCase().includes(q),
  );
}

/** Split a level's identities into the rows shown and the apps folded into one line. */
export function foldApps(
  identities: CapabilityIdentity[],
  maxInline = 3,
): { shown: CapabilityIdentity[]; folded: CapabilityIdentity[] } {
  const apps = identities.filter((i) => i.kind === "app" || i.kind === "other");
  if (apps.length <= maxInline) return { shown: identities, folded: [] };
  return { shown: identities.filter((i) => !apps.includes(i)), folded: apps };
}

/** "1 person · 2 groups · 5 apps" for a level card; "nobody" when empty. */
export function levelCounts(group: CapabilityLevelGroup): string {
  const parts = [
    group.people ? plural(group.people, "person", "people") : null,
    group.groups ? plural(group.groups, "group") : null,
    group.apps ? plural(group.apps, "app") : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "nobody";
}
