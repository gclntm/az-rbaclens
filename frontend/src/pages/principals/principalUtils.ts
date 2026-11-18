import type { PrincipalAccessRow } from "@/api/client";

export type PrincipalTab = "access" | "groups" | "activity" | "findings";

export const PRINCIPAL_TABS: PrincipalTab[] = ["access", "groups", "activity", "findings"];

export const TYPE_LABEL: Record<string, string> = {
  User: "Person",
  Group: "Group",
  ServicePrincipal: "App",
  ForeignGroup: "Group from another tenant",
  Device: "Device",
};

export function typeLabel(type: string | null | undefined): string {
  return type ? (TYPE_LABEL[type] ?? type) : "Unknown type";
}

export function principalHref(principalId: string, tab?: PrincipalTab): string {
  const path = `/people/${encodeURIComponent(principalId.toLowerCase())}`;
  return tab && tab !== "access" ? `${path}?tab=${tab}` : path;
}

export function parsePrincipalTab(value: string | null): PrincipalTab {
  return PRINCIPAL_TABS.includes(value as PrincipalTab) ? (value as PrincipalTab) : "access";
}

export type SubscriptionGroup = {
  /** Subscription id, or "" for management-group / tenant scopes. */
  key: string;
  label: string;
  rows: PrincipalAccessRow[];
  highRisk: number;
};

/**
 * Access rows bucketed by subscription, management-group / tenant grants
 * first (they reach every subscription below), then subscriptions by name.
 * Row order within a bucket is kept from the API (level → scope → direct first).
 */
export function groupBySubscription(rows: PrincipalAccessRow[]): SubscriptionGroup[] {
  const groups = new Map<string, SubscriptionGroup>();
  for (const row of rows) {
    const key = row.subscription_id ?? "";
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        label: key ? (row.subscription_name ?? key) : "Management groups & tenant",
        rows: [],
        highRisk: 0,
      };
      groups.set(key, g);
    }
    g.rows.push(row);
    if (row.is_high_risk) g.highRisk += 1;
  }
  return [...groups.values()].sort((a, b) => {
    if (!a.key !== !b.key) return a.key ? 1 : -1;
    return a.label.localeCompare(b.label);
  });
}

export type AccessFilter = { source: "all" | "direct" | "group"; query: string; highRiskOnly: boolean };

export function filterAccess(rows: PrincipalAccessRow[], f: AccessFilter): PrincipalAccessRow[] {
  const needle = f.query.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (f.source === "all" || r.source === f.source) &&
      (!f.highRiskOnly || r.is_high_risk) &&
      (!needle ||
        [r.role_name, r.scope_name, r.scope, r.via_group_name, r.subscription_name].some((v) =>
          (v ?? "").toLowerCase().includes(needle),
        )),
  );
}

/**
 * Share of the grant's permissions exercised in the window. The operation
 * count is the principal's activity at *any* scope, so it only goes in the
 * tooltip ({@link usageTitle}) — "4 ops · 0 %" would read as a contradiction.
 */
export function usageLabel(row: PrincipalAccessRow): string {
  if (row.observed_op_count === null) return "—";
  if (row.observed_op_count === 0) return "no change recorded";
  return row.coverage_pct === null ? "active" : `${row.coverage_pct} % used`;
}

export function usageTitle(row: PrincipalAccessRow): string | undefined {
  if (!row.observed_op_count) return undefined;
  const who = row.source === "group" ? "the group's members" : "this identity";
  return `${row.observed_op_count} kind${row.observed_op_count === 1 ? "" : "s"} of change by ${who} in the last 90 days, anywhere`;
}

/** How an owner owns an app or group. */
export function viaLabel(via: string): string {
  if (via === "registration") return "owns its app registration";
  if (via === "service_principal") return "owns its service principal";
  if (via === "group") return "owns the group";
  return via;
}

/** What owning this identity allows, for the Owners section. */
export function ownersIntro(principalType: string | null | undefined): string {
  if (principalType === "ServicePrincipal")
    return "Whoever owns this app (its registration or its service principal) can add a secret to it and act as it, with everything it holds.";
  return "Whoever owns this group decides its members, themselves included, and so who gets the group's access.";
}
