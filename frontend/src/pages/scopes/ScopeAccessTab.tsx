import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { linkButtonClass, pillClass } from "@/components/chips";
import Button from "@/components/Button";
import {
  api,
  type AssignmentSource,
  type ScopeAssignments,
  type ScopeNode,
} from "@/api/client";
import ConditionTag from "@/components/ConditionTag";
import PimTag from "@/components/PimTag";
import { formatDay } from "./whoUtils";
import { grantHref, originText } from "@/pages/grants/grantUtils";
import { principalHref, typeLabel } from "@/pages/principals/principalUtils";
import { RoleLink } from "@/components/RoleDetailDrawer";
import ScopeTree from "@/components/ScopeTree";
import { SectionCard } from "@/components/PageTemplate";
import {
  BADGE,
  BADGE_LEVEL,
  CAPTION,
  CARD,
  CELL_PRINCIPAL,
  CELL_ROLE,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  FILTER_BAR,
  FILTER_SEP,
  SEV_DOT,
  SEV_DOT_TONE,
  TABLE_COMPACT,
  TABLE_FOOT,
  TABLE_ROW_CLICKABLE,
  TABLE_ROW_SELECTED,
  TABLE_SCROLL,
  TAG,
  TAG_TONE,
} from "@/components/classes";
import { cx } from "@/components/classes";

import AssignmentDrawer from "./AssignmentDrawer";
import {
  coverageTone,
  DEFAULT_ASSIGNMENT_FILTER,
  filterAssignments,
  isBroadHighRisk,
  LEVEL_SHORT,
  principalLabel,
  scopeHref,
  scopeLabel,
  SOURCE_LABEL,
  worstOpenSeverity,
  type AssignmentFilter,
} from "./scopeUtils";

const PAGE = 50;
const SOURCES: AssignmentSource[] = ["direct", "inherited", "below"];

/** Where a grant comes from, as a badge colour. */
const BADGE_SOURCE: Record<string, string> = {
  direct: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  inherited: "bg-(--accent-purple-subtle) text-(--accent-purple)",
  below: "bg-primary_hover text-tertiary",
};
const SEARCH_INPUT = "flex-[1_1_240px] max-w-[360px]";
/** A bar with its value, or a dot with a count. */
const COV = "inline-flex items-center gap-2 whitespace-nowrap";
const COV_BAR: Record<string, string> = {
  none: "bg-(--accent-orange)",
  low: "bg-(--accent-orange)",
  ok: "bg-(--accent-green)",
};

function Coverage({ pct, isGroupUnknown }: { pct: number | null; isGroupUnknown: boolean }) {
  if (isGroupUnknown) return <span className="text-quaternary" title="Group membership unresolved">?</span>;
  const tone = coverageTone(pct);
  if (tone === "unknown") return <span className="text-quaternary" title="Coverage not computed">—</span>;
  return (
    <span className={COV} title={`${pct} % of the role's permissions used for changes in 90 days`}>
      <span className="h-1.5 w-14 overflow-hidden rounded-[3px] bg-primary_hover" aria-hidden="true">
        <span className={cx("block h-full rounded-[3px]", COV_BAR[tone])} style={{ width: `${Math.max(pct ?? 0, 2)}%` }} />
      </span>
      <span className={tone === "none" ? "font-semibold text-warning-primary" : undefined}>{pct} %</span>
    </span>
  );
}

export default function ScopeAccessTab({
  scope,
  assignments,
}: {
  scope: ScopeNode;
  assignments: ScopeAssignments;
}) {
  const [subtree, setSubtree] = useState<ScopeNode[] | null>(null);
  const [searchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") ?? "";
  const [filter, setFilter] = useState<AssignmentFilter>({
    ...DEFAULT_ASSIGNMENT_FILTER,
    query: initialQuery,
  });
  const [limit, setLimit] = useState(PAGE);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  useEffect(() => {
    setSubtree(null);
    setSelectedId(null);
    setFilter({ ...DEFAULT_ASSIGNMENT_FILTER, query: initialQuery });
    api.scopes
      .tree({ root: scope.id })
      .then(setSubtree)
      .catch(() => setSubtree([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reseed only on scope change
  }, [scope.id]);

  const rows = useMemo(() => filterAssignments(assignments.rows, filter), [assignments.rows, filter]);
  const visible = rows.slice(0, limit);
  const selected = assignments.rows.find((r) => r.assignment_id === selectedId) ?? null;
  const update = (patch: Partial<AssignmentFilter>) => {
    setFilter((f) => ({ ...f, ...patch }));
    setLimit(PAGE);
  };
  const total = assignments.rows.length;

  return (
    <section className="flex flex-wrap items-start gap-4">
      <nav className={cx(CARD, "min-w-[260px] flex-[0_1_320px] p-4")} aria-label="This place and what is inside it">
        {subtree === null ? (
          <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>Loading…</p>
        ) : (
          <ScopeTree nodes={subtree} selectedId={scope.id} compact initialDepth={1} label="Place" />
        )}
      </nav>

      <SectionCard
        className="min-w-0 flex-[999_1_520px]"
        icon="shield"
        title="All grants"
        hint="Every grant that applies here: given here, from a place above, or on a place inside. Open one for its details."
      >
        <div className={FILTER_BAR}>
          <input
            type="search"
            className={cx("font-normal tracking-normal", SEARCH_INPUT)}
            placeholder="Find a person, app, role or place"
            aria-label="Find a grant"
            value={filter.query}
            onChange={(e) => update({ query: e.target.value })}
          />
          <button
            type="button"
            className={pillClass(filter.source === "all")}
            aria-pressed={filter.source === "all"}
            onClick={() => update({ source: "all" })}
          >
            All <span className="text-quaternary">{total}</span>
          </button>
          {SOURCES.map((s) => (
            <button
              key={s}
              type="button"
              className={pillClass(filter.source === s)}
              aria-pressed={filter.source === s}
              onClick={() => update({ source: s })}
              disabled={assignments.counts[s] === 0}
            >
              {SOURCE_LABEL[s]} <span className="text-quaternary">{assignments.counts[s]}</span>
            </button>
          ))}
          <span className={FILTER_SEP} aria-hidden="true" />
          <label className={pillClass(filter.highRiskOnly)}>
            <input
              type="checkbox"
              checked={filter.highRiskOnly}
              onChange={(e) => update({ highRiskOnly: e.target.checked })}
            />
            High-risk only
          </label>
          <label className={pillClass(filter.withFindingsOnly)}>
            <input
              type="checkbox"
              checked={filter.withFindingsOnly}
              onChange={(e) => update({ withFindingsOnly: e.target.checked })}
            />
            With open findings
          </label>
        </div>

        {rows.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>
            {total === 0 ? "No grant applies to this place." : "No grant matches the filters."}
          </p>
        ) : (
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Who</th>
                  <th scope="col">Role</th>
                  <th scope="col">Where</th>
                  <th scope="col">How</th>
                  <th scope="col">Since</th>
                  <th scope="col" title="Share of the role's permissions used for changes in the last 90 days. Reads are never recorded.">
                    Changes · 90 d
                  </th>
                  <th scope="col">Needs a look</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const worst = worstOpenSeverity(r);
                  const openCount = r.findings.filter((f) => f.status === "open").length;
                  const isSel = r.assignment_id === selectedId;
                  return (
                    <tr
                      key={r.assignment_id}
                      className={cx(TABLE_ROW_CLICKABLE, isSel && TABLE_ROW_SELECTED)}
                      onClick={() => setSelectedId(isSel ? null : r.assignment_id)}
                      aria-selected={isSel}
                    >
                      <td className={CELL_PRINCIPAL}>
                        <div className={CELL_STACK}>
                          <button
                            type="button"
                            className={linkButtonClass()}
                            style={{ textAlign: "left", fontWeight: 500 }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedId(isSel ? null : r.assignment_id);
                            }}
                          >
                            {principalLabel(r)}
                          </button>
                          <span className={CELL_SUB}>
                            {typeLabel(r.principal_type)}
                            {!r.principal_name && (
                              <>
                                {" "}
                                <span className={cx(TAG, TAG_TONE.unresolved)}>no longer in Entra ID?</span>
                              </>
                            )}
                          </span>
                        </div>
                      </td>
                      <td className={CELL_ROLE} onClick={(e) => e.stopPropagation()}>
                        <RoleLink roleId={r.role_definition_id} roleName={r.role_name} />{" "}
                        {isBroadHighRisk(r) && (
                          <span className={cx(TAG, TAG_TONE.risk)} title="Powerful role given at management-group or subscription level">
                            R
                          </span>
                        )}{" "}
                        {r.role_type === "CustomRole" && (
                          <span className={cx(TAG, TAG_TONE.custom)} title="Custom role">
                            C
                          </span>
                        )}{" "}
                        <ConditionTag kind={r.condition_kind} text={r.condition_text} />{" "}
                        <PimTag activated={r.activated} until={r.until} />
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className={cx(BADGE, BADGE_LEVEL[r.scope_level])}>{LEVEL_SHORT[r.scope_level]}</span>{" "}
                        <Link to={scopeHref(r.scope)} title={r.scope}>
                          {scopeLabel(r.scope, r.scope_name)}
                        </Link>
                      </td>
                      <td>
                        <span className={cx(BADGE, BADGE_SOURCE[r.source])}>{SOURCE_LABEL[r.source]}</span>
                      </td>
                      <td className="min-w-[12rem] text-[0.8125rem] text-tertiary [overflow-wrap:break-word]" onClick={(e) => e.stopPropagation()}>
                        {originText(r) ?? <span className="text-quaternary">not recorded</span>}
                        {r.grant_id && (
                          <>
                            {" · "}
                            <Link to={grantHref(r.grant_id)}>details</Link>
                          </>
                        )}
                      </td>
                      <td>
                        <Coverage
                          pct={r.coverage_pct}
                          isGroupUnknown={r.principal_type === "Group" && r.member_count === null && r.coverage_pct !== null}
                        />
                      </td>
                      <td>
                        {worst ? (
                          <span className={COV} title={`${openCount} open, worst ${worst}`}>
                            <span className={cx(SEV_DOT, SEV_DOT_TONE[worst])} aria-hidden="true" />
                            {openCount}
                          </span>
                        ) : (
                          <span className="text-quaternary">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className={TABLE_FOOT}>
          <span>
            Showing {visible.length} of {rows.length}
            {rows.length !== total && ` (filtered from ${total})`} · given here, then from above, then
            inside; powerful first
          </span>
          {rows.length > visible.length && (
            <Button size="sm" type="button" onClick={() => setLimit((l) => l + PAGE)}>
              Show {Math.min(PAGE, rows.length - visible.length)} more
            </Button>
          )}
        </div>
      </SectionCard>

      {assignments.eligible.length > 0 && (
        <SectionCard
          className="min-w-0 flex-[1_1_100%]"
          icon="calendar"
          title={`Eligible through PIM (${assignments.eligible.length})`}
          hint="Not active: these identities can switch the role on when needed, usually with approval or MFA and for a limited time."
        >
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Who</th>
                  <th scope="col">Can activate</th>
                  <th scope="col">Where</th>
                  <th scope="col">How</th>
                  <th scope="col">Eligible until</th>
                </tr>
              </thead>
              <tbody>
                {assignments.eligible.map((e) => (
                  <tr key={`${e.principal_id}-${e.role_definition_id}-${e.scope}`}>
                    <td className={CELL_PRINCIPAL}>
                      <div className={CELL_STACK}>
                        <Link to={principalHref(e.principal_id)}>{e.principal_name ?? e.principal_id}</Link>
                        <span className={CELL_SUB}>
                          {typeLabel(e.principal_type)}
                          {e.member_type === "Group" && " · through a group"}
                        </span>
                      </div>
                    </td>
                    <td className={CELL_ROLE}>
                      <RoleLink roleId={e.role_definition_id} roleName={e.role_name} />{" "}
                      <ConditionTag kind={e.condition_kind} text={e.condition_text} />
                    </td>
                    <td>
                      <span className={cx(BADGE, BADGE_LEVEL[e.scope_level])}>{LEVEL_SHORT[e.scope_level]}</span>{" "}
                      <Link to={scopeHref(e.scope)} title={e.scope}>
                        {scopeLabel(e.scope, e.scope_name)}
                      </Link>
                    </td>
                    <td>
                      <span className={cx(BADGE, BADGE_SOURCE[e.source])}>{SOURCE_LABEL[e.source]}</span>
                    </td>
                    <td>{e.until ? formatDay(e.until) : <span className="text-quaternary">no end date</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {selected && <AssignmentDrawer row={selected} onClose={() => setSelectedId(null)} />}
    </section>
  );
}
