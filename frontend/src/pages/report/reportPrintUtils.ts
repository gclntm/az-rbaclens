import type { Report, ReportGrant, ReportNumbers, ReportRecommendation } from "@/api/client";

/** One printed row: an identity with one role, on one or more places. */
export type GrantRow = {
  key: string;
  who: string;
  kind: ReportGrant["kind"];
  role: string;
  pim: boolean;
  where: string[];
};

/**
 * Same identity, role and how-given on several places → one row listing the places,
 * in the order the grants came (broadest first).
 */
export function groupGrants(grants: ReportGrant[], place: (g: ReportGrant) => string): GrantRow[] {
  const rows = new Map<string, GrantRow>();
  for (const g of grants) {
    const role = g.role_name ?? "—";
    const pim = g.how !== "standing";
    const key = `${g.principal_id}|${role}|${pim}`;
    const row = rows.get(key);
    if (row) row.where.push(place(g));
    else
      rows.set(key, {
        key,
        who: g.principal_name ?? g.principal_id,
        kind: g.kind,
        role,
        pim,
        where: [place(g)],
      });
  }
  return [...rows.values()];
}

/** "infra, test, train, +3". */
export function placesText(where: string[], max = 4): string {
  if (where.length <= max) return where.join(", ");
  return `${where.slice(0, max - 1).join(", ")}, +${where.length - (max - 1)}`;
}

/** Rows to print for a recommendation: every must-do, the first few should-do. */
export function rowsToPrint(rows: GrantRow[], weight: ReportRecommendation["weight"], shouldMax = 8): GrantRow[] {
  return weight === "must" ? rows : rows.slice(0, shouldMax);
}

/** "all 12 shown" or "28 of 30 shown, all in the CSV annex". */
export function shownText(rows: GrantRow[], count: number): string {
  const covered = rows.reduce((n, r) => n + r.where.length, 0);
  return covered >= count ? `all ${count} shown` : `${covered} of ${count} shown, all in the CSV annex`;
}

export type PrivilegedCell = { mg: number; sub: number; action: number };
export type PrivilegedSummary = { access: PrivilegedCell; keys: PrivilegedCell; total: number; fine: number };

/** The privileged list as counts: what it allows × where, and how many need action. */
export function privilegedSummary(grants: ReportGrant[]): PrivilegedSummary {
  const cell = (): PrivilegedCell => ({ mg: 0, sub: 0, action: 0 });
  const out: PrivilegedSummary = { access: cell(), keys: cell(), total: grants.length, fine: 0 };
  for (const g of grants) {
    const c = g.level === "access" ? out.access : g.level === "keys" ? out.keys : null;
    if (!c) continue;
    if (g.scope_level === "mgmt_group") c.mg += 1;
    else c.sub += 1;
    if (g.advice_weight === "must" || g.advice_weight === "should") c.action += 1;
    else out.fine += 1;
  }
  return out;
}

/** Change against a saved assessment, for a "lower is better" number: "+2", "−3", "= baseline" or "". */
export function deltaText(now: number | null | undefined, before: number | null | undefined): string {
  if (now === null || now === undefined || before === null || before === undefined) return "";
  const d = now - before;
  if (d === 0) return "= baseline";
  return d > 0 ? `+${d} since baseline` : `−${-d} since baseline`;
}

/** "better" when a lower-is-better number went down, "worse" when it went up. */
export function deltaTrend(now: number | null | undefined, before: number | null | undefined): "better" | "worse" | null {
  if (now === null || now === undefined || before === null || before === undefined || now === before) return null;
  return now < before ? "better" : "worse";
}

/** The one figure a control tile shows; falls back to its first evidence sentence. */
export function controlFigure(control: string, r: Report): string {
  const n: ReportNumbers = r.numbers;
  switch (control) {
    case "8.2":
      return `${n.standing_admin_high ?? 0} standing grants can change access high up`;
    case "5.16":
      return `${n.orphans ?? 0} grants to identities that no longer exist`;
    case "5.15":
      return `${n.least_privilege ?? 0} grants larger than needed`;
    case "5.17":
      return `${n.keys_high ?? 0} standing grants can read keys high up`;
    case "5.18":
      return `${r.reviews.subscriptions_with_responsible} of ${r.reviews.subscriptions} subscriptions have someone responsible`;
    case "8.15":
      return `${r.changes.subscriptions_collected_hourly} of ${r.reviews.subscriptions} subscriptions logged hourly`;
    default:
      return r.controls.find((c) => c.control === control)?.evidence[0] ?? "";
  }
}

/** The recommendations worth acting on, must-do first (the API's order). */
export function actionRecs(r: Report): ReportRecommendation[] {
  return r.recommendations.filter((rec) => rec.weight === "must" || rec.weight === "should");
}
