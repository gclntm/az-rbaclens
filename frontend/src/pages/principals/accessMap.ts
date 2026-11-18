import type { CapabilityLevel, PrincipalAccessRow, ScopeLevel, ScopeNode } from "@/api/client";

const RANK: Record<CapabilityLevel, number> = { access: 0, keys: 1, change: 2, data: 3, view: 4, unknown: 5 };
const LEVEL_ORDER: Record<ScopeLevel, number> = { directory: -1, mgmt_group: 0, subscription: 1, resource_group: 2, resource: 3 };
export const POWERFUL: CapabilityLevel[] = ["access", "keys"];

export type PlaceMeta = { name: string | null; level: ScopeLevel; parent: string | null };

export type MapRow = {
  id: string;
  name: string | null;
  level: ScopeLevel;
  depth: number;
  /** Grants given on this place, strongest first. */
  grants: PrincipalAccessRow[];
  /** The strongest grant given above that also applies here. */
  fromAbove: { grant: PrincipalAccessRow; place: string | null } | null;
  /** Powerful access (change access / keys) covering this place: the rail's colour. */
  rail: "access" | "keys" | null;
  /** This place is where that rail starts. */
  railStarts: boolean;
};

/** Every place in the scope tree: id → name, level, parent. */
export function flattenTree(nodes: ScopeNode[]): Map<string, PlaceMeta> {
  const out = new Map<string, PlaceMeta>();
  const walk = (list: ScopeNode[]) => {
    for (const n of list) {
      out.set(n.id.toLowerCase(), { name: n.name, level: n.level, parent: n.parent_id?.toLowerCase() ?? null });
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}

function nearestKnown(scope: string, places: Map<string, PlaceMeta>): string | null {
  let cur = scope;
  while (cur.includes("/")) {
    cur = cur.slice(0, cur.lastIndexOf("/"));
    if (places.has(cur)) return cur;
  }
  return null;
}

const stronger = (a: PrincipalAccessRow, b: PrincipalAccessRow) => RANK[a.level ?? "unknown"] - RANK[b.level ?? "unknown"];

/**
 * The places an identity's grants apply to, as a tree: every place a grant
 * was given on, plus the places above it, depth-first. Places the scope tree
 * does not list (single resources) hang under their nearest known place.
 */
export function buildAccessMap(rows: PrincipalAccessRow[], places: Map<string, PlaceMeta>): MapRow[] {
  const meta = new Map(places);
  const byScope = new Map<string, PrincipalAccessRow[]>();
  const needed = new Set<string>();
  for (const r of rows) {
    const id = r.scope.toLowerCase();
    byScope.set(id, [...(byScope.get(id) ?? []), r]);
    if (!meta.has(id)) {
      meta.set(id, { name: r.scope_name, level: r.scope_level, parent: nearestKnown(id, places) });
    }
    let cur: string | null = id;
    while (cur && !needed.has(cur)) {
      needed.add(cur);
      cur = meta.get(cur)?.parent ?? null;
    }
  }
  const children = new Map<string | null, string[]>();
  for (const id of needed) {
    const parent = meta.get(id)?.parent ?? null;
    const key = parent && needed.has(parent) ? parent : null;
    children.set(key, [...(children.get(key) ?? []), id]);
  }
  const order = (a: string, b: string) => {
    const ma = meta.get(a)!;
    const mb = meta.get(b)!;
    return LEVEL_ORDER[ma.level] - LEVEL_ORDER[mb.level] || (ma.name ?? a).localeCompare(mb.name ?? b);
  };
  const out: MapRow[] = [];
  const walk = (
    id: string,
    depth: number,
    above: { grant: PrincipalAccessRow; place: string | null } | null,
    rail: MapRow["rail"],
  ) => {
    const m = meta.get(id)!;
    const grants = [...(byScope.get(id) ?? [])].sort(stronger);
    const ownPowerful = grants.find((g) => POWERFUL.includes(g.level));
    const ownRail = (ownPowerful?.level as MapRow["rail"]) ?? null;
    const nextRail = !rail ? ownRail : !ownRail ? rail : RANK[ownRail] < RANK[rail] ? ownRail : rail;
    out.push({ id, name: m.name, level: m.level, depth, grants, fromAbove: above, rail: nextRail, railStarts: !!ownRail && nextRail !== rail });
    const best = grants[0];
    const nextAbove = best && (!above || stronger(best, above.grant) < 0) ? { grant: best, place: m.name } : above;
    for (const c of (children.get(id) ?? []).sort(order)) walk(c, depth + 1, nextAbove, nextRail);
  };
  for (const root of (children.get(null) ?? []).sort(order)) walk(root, 0, null, null);
  return out;
}
