import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api, type AdviceWeight, type CapabilityLevel, type PrincipalAccessRow } from "@/api/client";
import { EMPTY_STATE, LEVEL_TAG } from "@/components/classes";
import NavIcon from "@/components/NavIcon";
import TreeLines from "@/components/tree/TreeLines";
import { treeGuides } from "@/components/tree/treeGuides";
import { formatDay, grantHref, originText } from "@/pages/grants/grantUtils";
import { LEVEL_LABEL, LEVEL_SHORT, scopeHref, treeLabel } from "@/pages/scopes/scopeUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";


import { buildAccessMap, flattenTree, type PlaceMeta } from "./accessMap";

/** A grant as a chip, coloured by what it allows (put LEVEL_CHIP[level] beside it). */
export const AM_CHIP =
  "inline-flex max-w-full items-center gap-[5px] rounded-full bg-(--lc-bg) px-2.5 py-0.5 text-[0.8125rem] leading-[1.5] font-medium text-(--lc-fg) " +
  "[&_a]:inline-flex [&_a]:min-w-0 [&_a]:items-center [&_a]:gap-[5px] [&_a]:text-inherit [&_a]:no-underline [&_a:hover_span:first-of-type]:underline " +
  "[&_span]:overflow-hidden [&_span]:text-ellipsis [&_span]:whitespace-nowrap";

/** The chip colours by capability level, as the --lc-bg / --lc-fg variables the old .lc-* classes set. */
export const LEVEL_CHIP: Record<CapabilityLevel, string> = {
  access: "[--lc-bg:var(--accent-red-subtle)] [--lc-fg:var(--accent-red)]",
  keys: "[--lc-bg:var(--accent-orange-subtle)] [--lc-fg:var(--accent-orange)]",
  change: "[--lc-bg:var(--accent-blue-subtle)] [--lc-fg:var(--accent-blue)]",
  data: "[--lc-bg:var(--accent-purple-subtle)] [--lc-fg:var(--accent-purple)]",
  view: "[--lc-bg:var(--chip-bg)] [--lc-fg:var(--text-secondary)]",
  unknown: "[--lc-bg:var(--chip-bg)] [--lc-fg:var(--text-secondary)]",
};

const AM_ROW =
  "relative grid min-h-10 grid-cols-[minmax(180px,0.9fr)_minmax(0,1.4fr)] items-center gap-4 border-b border-b-secondary py-0 pr-2 pl-3.5 last:border-b-0 " +
  "max-[720px]:grid-cols-[minmax(0,1fr)] max-[720px]:gap-0 max-[720px]:pt-1";
/** The rail down the left of a row covered by powerful access. */
const AM_RAIL =
  "before:absolute before:top-0 before:bottom-0 before:left-0.5 before:w-1 before:bg-(--am-rail) before:opacity-85 before:content-['']";
const AM_RAIL_START = "before:top-2 before:rounded-t-[2px]";
const RAIL_COLOUR: Record<"access" | "keys", string> = {
  access: "[--am-rail:var(--accent-red)]",
  keys: "[--am-rail:var(--accent-orange)]",
};
const AM_VIA = "font-normal opacity-85";
const ADVICE_DOT = "inline-block size-2 rounded-full bg-(--text-muted) align-middle";
const ADVICE_DOT_TONE: Record<AdviceWeight, string> = {
  must: "bg-(--accent-red)",
  should: "bg-(--accent-orange)",
  could: "bg-(--accent-blue)",
  info: "",
};

/**
 * An identity's grants as a map of places: each grant is a chip on the
 * place where it was given, coloured by what it allows; a rail runs down
 * from powerful access (change access, keys) over everything it covers.
 */
export default function AccessMapView({ rows }: { rows: PrincipalAccessRow[] }) {
  const [places, setPlaces] = useState<Map<string, PlaceMeta> | null>(null);

  useEffect(() => {
    api.scopes
      .tree()
      .then((t) => setPlaces(flattenTree(t)))
      .catch(() => setPlaces(new Map()));
  }, []);

  const map = useMemo(() => (places ? buildAccessMap(rows, places) : []), [rows, places]);
  const guides = useMemo(() => treeGuides(map), [map]);

  if (!places) return <p className="my-[1em] text-quaternary">Drawing the map…</p>;
  if (map.length === 0) return <p className={cx("my-[1em]", EMPTY_STATE)}>No grant matches.</p>;

  return (
    <ul className="mx-0 mt-2 mb-0 list-none p-0 text-[0.875rem]" aria-label="Places this identity can act on">
      {map.map((r, i) => (
        <li
          key={r.id}
          className={cx(AM_ROW, r.rail && AM_RAIL, r.rail && RAIL_COLOUR[r.rail], r.railStarts && AM_RAIL_START)}
        >
          <span className="flex min-w-0 items-center gap-1.5 self-stretch">
            <TreeLines {...guides[i]} />
            <span className={LEVEL_TAG} title={LEVEL_LABEL[r.level]}>
              {LEVEL_SHORT[r.level]}
            </span>
            <Link
              to={scopeHref(r.id)}
              title={r.id}
              className="overflow-hidden text-ellipsis whitespace-nowrap text-primary no-underline hover:text-(--accent-blue)"
            >
              {treeLabel(r.id, r.name, r.level)}
            </Link>
          </span>
          <span className="flex flex-wrap justify-start gap-1.5 px-0 py-1.5 max-[720px]:pt-0 max-[720px]:pb-2 max-[720px]:pl-[22px]">
            {r.grants.length > 0
              ? r.grants.map((g) => <GrantChip key={`${g.role_definition_id}-${g.via_group_id ?? ""}`} grant={g} />)
              : r.fromAbove && (
                  <span className="text-[0.8125rem] text-tertiary">
                    {r.fromAbove.grant.role_name ?? "a role"} from {r.fromAbove.place ?? "above"}
                  </span>
                )}
          </span>
        </li>
      ))}
    </ul>
  );
}

function GrantChip({ grant: g }: { grant: PrincipalAccessRow }) {
  const level = g.level ?? "unknown";
  const tip = [
    LEVEL_INFO[level].title,
    originText(g),
    g.source === "group" ? `through ${g.via_group_name ?? g.via_group_id}` : "given directly",
    g.condition_text ? `limited: ${g.condition_text}` : null,
    g.advice_title && g.advice_weight !== "info" ? `advice: ${g.advice_title}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const label = (
    <>
      {g.advice_weight && g.advice_weight !== "info" && (
        <span className={cx(ADVICE_DOT, ADVICE_DOT_TONE[g.advice_weight])} title={g.advice_title ?? undefined} />
      )}
      {g.source === "group" && <NavIcon name="people" size={13} />}
      <span>{g.role_name ?? "Unnamed role"}</span>
      {g.source === "group" && <span className={AM_VIA}>via {g.via_group_name ?? "a group"}</span>}
      {g.activated && <span className={AM_VIA}>PIM</span>}
      {g.until && <span className={AM_VIA}>until {formatDay(g.until)}</span>}
      {g.condition_kind && <span className={AM_VIA}>limited</span>}
    </>
  );
  return (
    <span className={cx(AM_CHIP, LEVEL_CHIP[level])} title={tip}>
      {g.grant_id ? <Link to={grantHref(g.grant_id)}>{label}</Link> : label}
    </span>
  );
}
