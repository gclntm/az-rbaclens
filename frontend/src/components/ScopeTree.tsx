import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { ScopeNode } from "@/api/client";
import { cx, EMPTY_STATE, LEVEL_TAG, NUM } from "@/components/classes";
import LevelBar from "@/components/tree/LevelBar";
import TreeLines from "@/components/tree/TreeLines";
import { treeGuides } from "@/components/tree/treeGuides";
import {
  ageLabel,
  allIds,
  expandToDepth,
  filterTree,
  freshnessOf,
  LEVEL_LABEL,
  LEVEL_SHORT,
  scopeHref,
  scopeLabel,
  treeLabel,
  visibleTreeRows,
} from "@/pages/scopes/scopeUtils";

/** Columns: name, grants by level, last check (narrow screens: name, grants); compact: name, count. */
const COLS = "grid-cols-[minmax(0,1fr)_minmax(80px,140px)_64px] max-[720px]:grid-cols-[minmax(0,1fr)_72px]";
const COLS_COMPACT = "grid-cols-[minmax(0,1fr)_48px]";
const HEAD = "grid items-center gap-2 px-2 pt-0 pb-1 text-[0.6875rem] font-semibold tracking-[0.06em] text-tertiary uppercase";
const ROW = "grid min-h-9 items-center gap-2 rounded-[4px] px-2 hover:bg-primary_hover";
const ROW_SELECTED = "bg-(--chip-active-bg) hover:bg-(--chip-active-bg)";
const TOGGLE =
  "inline-flex size-[22px] flex-[0_0_22px] cursor-pointer items-center justify-center rounded-[2px] [border:0] bg-transparent p-0 text-tertiary " +
  "hover:bg-primary_hover hover:text-primary [&_svg]:size-3.5 [&_svg]:[transition:transform_80ms_ease] aria-expanded:[&_svg]:[transform:rotate(90deg)]";
/** How fresh a place's last check is: the dot, and the text beside it. */
const FRESH_DOT: Record<string, string> = {
  fresh: "bg-(--accent-green)",
  aging: "bg-(--accent-orange)",
  stale: "bg-(--accent-red)",
  never: "border border-(--text-muted) bg-transparent",
};
const AGE: Record<string, string> = {
  aging: "text-warning-primary",
  stale: "text-error-primary",
  never: "text-quaternary",
};

function Chevron() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

/**
 * Collapsible scope hierarchy with per-node stats from the current RA set.
 *
 * Connector lines show the hierarchy; each place shows a bar of the grants
 * given there and inside it, by what they allow.
 * ``compact`` keeps only the grant count (the place page's side tree).
 * ``filter`` narrows to matching nodes plus their ancestors, fully expanded.
 */
export default function ScopeTree({
  nodes,
  selectedId,
  compact = false,
  filter = "",
  initialDepth = 2,
  label = "Places",
  responsible,
}: {
  nodes: ScopeNode[];
  /** Places where someone is set as responsible: scope id → name. */
  responsible?: ReadonlyMap<string, string>;
  selectedId?: string;
  compact?: boolean;
  filter?: string;
  initialDepth?: number;
  label?: string;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => expandToDepth(nodes, initialDepth));

  // Re-seed when the data set changes (navigating between scopes).
  useEffect(() => {
    setExpanded(expandToDepth(nodes, initialDepth));
  }, [nodes, initialDepth]);

  const filtered = useMemo(() => filterTree(nodes, filter), [nodes, filter]);
  const shown = filtered ?? nodes;
  const open = filtered ? allIds(filtered) : expanded;
  const rows = useMemo(() => visibleTreeRows(shown, open), [shown, open]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const guides = useMemo(() => treeGuides(rows), [rows]);

  if (rows.length === 0) {
    return <p className={cx("my-[1em]", EMPTY_STATE)}>{filter ? "No place matches." : "No places yet. They appear after the first daily check."}</p>;
  }

  return (
    <div>
      <div className={cx(HEAD, compact ? COLS_COMPACT : COLS)} aria-hidden="true">
        <span>{label}</span>
        {compact ? (
          <span className={NUM}>Grants</span>
        ) : (
          <>
            <span title="Grants given here and inside, by what they allow">Who can do what</span>
            <span className={cx(NUM, "max-[720px]:hidden")}>Checked</span>
          </>
        )}
      </div>
      <ul className="m-0 list-none p-0 text-[0.8125rem]" aria-label={label}>
        {rows.map(({ node, hasChildren, expanded: isOpen }, i) => {
          const fresh = freshnessOf(node.latest_scan?.age_days);
          const age = node.latest_scan?.age_days ?? null;
          const name = treeLabel(node.id, node.name, node.level);
          const person = responsible?.get(node.id);
          return (
            <li key={node.id} className={cx(ROW, compact ? COLS_COMPACT : COLS, node.id === selectedId && ROW_SELECTED)}>
              <span className="flex min-w-0 items-center gap-1.5 self-stretch">
                <TreeLines {...guides[i]} />
                {hasChildren ? (
                  <button
                    type="button"
                    className={cx("text-[13.3333px] leading-[normal] font-normal tracking-normal", TOGGLE)}
                    aria-expanded={isOpen}
                    aria-label={`${isOpen ? "Collapse" : "Expand"} ${scopeLabel(node.id, node.name)}`}
                    onClick={() => toggle(node.id)}
                    disabled={filtered !== null}
                  >
                    <Chevron />
                  </button>
                ) : (
                  <span className="w-[22px] flex-[0_0_22px]" />
                )}
                <span className={LEVEL_TAG} title={LEVEL_LABEL[node.level]}>
                  {LEVEL_SHORT[node.level]}
                </span>
                <Link
                  to={scopeHref(node.id)}
                  className="overflow-hidden text-ellipsis whitespace-nowrap text-primary no-underline hover:text-(--accent-blue)"
                  title={`${scopeLabel(node.id, node.name)}\n${node.id}`}
                  aria-current={node.id === selectedId ? "page" : undefined}
                >
                  {name}
                </Link>
                {!compact && person && <span className="flex-none text-[0.75rem] whitespace-nowrap text-tertiary max-[720px]:hidden">· {person}</span>}
                {!compact && node.stats.open_findings > 0 && (
                  <span className="flex-none rounded-full bg-(--accent-red-subtle) px-[7px] text-[0.6875rem] font-semibold whitespace-nowrap text-error-primary" title={`${node.stats.open_findings} to look at`}>
                    {node.stats.open_findings}
                    <span className="max-[720px]:hidden"> to look at</span>
                  </span>
                )}
              </span>
              {compact ? (
                <span className={NUM}>{node.stats.assignments_below || "—"}</span>
              ) : (
                <>
                  <LevelBar levels={node.stats.levels_below ?? {}} />
                  <span className={cx("flex items-center justify-end gap-1.5 text-[0.75rem] max-[720px]:hidden", AGE[fresh])} title={`Checked: ${ageLabel(age)}`}>
                    <span className={cx("inline-block size-2 flex-[0_0_8px] rounded-full", FRESH_DOT[fresh])} aria-hidden="true" />
                    {age === null ? "never" : age === 0 ? "today" : `${age} d`}
                  </span>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
