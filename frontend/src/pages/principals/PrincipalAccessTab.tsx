import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { pillClass } from "@/components/chips";
import type { PrincipalPage } from "@/api/client";
import {
  BADGE,
  BADGE_LEVEL,
  CAPTION,
  CELL_ROLE,
  EMPTY_STATE,
  FILTER_BAR,
  FILTER_SEP,
  SECTION_TITLE,
  TABLE_COMPACT,
  TABLE_FOOT,
  TABLE_SCROLL,
  TAG,
  TAG_TONE,
} from "@/components/classes";
import ConditionTag from "@/components/ConditionTag";
import PimTag from "@/components/PimTag";
import { grantHref, originText } from "@/pages/grants/grantUtils";
import { formatDay } from "@/pages/scopes/whoUtils";
import { RoleLink } from "@/components/RoleDetailDrawer";
import { SectionCard } from "@/components/PageTemplate";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { LEVEL_SHORT, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import AccessMapView, { AM_CHIP, LEVEL_CHIP } from "./AccessMapView";
import {
  filterAccess,
  groupBySubscription,
  principalHref,
  usageLabel,
  usageTitle,
  type AccessFilter,
} from "./principalUtils";

/** How a grant reaches them, as a badge colour. */
const SOURCE_TONE = {
  direct: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  inherited: "bg-(--accent-purple-subtle) text-(--accent-purple)",
} as const;
const SEARCH_INPUT = "flex-[1_1_240px] max-w-[360px]";
/** The grant origin line in a table cell. */
const ORIGIN_CELL = "min-w-[12rem] text-[0.8125rem] text-tertiary [overflow-wrap:break-word]";

const SOURCES: { key: AccessFilter["source"]; label: string }[] = [
  { key: "all", label: "All" },
  { key: "direct", label: "Directly" },
  { key: "group", label: "Through a group" },
];

export default function PrincipalAccessTab({ data }: { data: PrincipalPage }) {
  const [filter, setFilter] = useState<AccessFilter>({ source: "all", query: "", highRiskOnly: false });
  const rows = useMemo(() => filterAccess(data.assignments, filter), [data.assignments, filter]);
  const groups = useMemo(() => groupBySubscription(rows), [rows]);
  const update = (patch: Partial<AccessFilter>) => setFilter((f) => ({ ...f, ...patch }));
  const [view, setView] = useState<"map" | "table">("map");

  return (
    <SectionCard
      icon="shield"
      title="What they can do"
      hint="Every grant they hold, in their own name or through a group, by subscription."
    >
      <div className={FILTER_BAR}>
        <input
          type="search"
          className={cx("font-normal tracking-normal", SEARCH_INPUT)}
          placeholder="Role, place or group"
          aria-label="Filter what they can do"
          value={filter.query}
          onChange={(e) => update({ query: e.target.value })}
        />
        {SOURCES.map((s) => (
          <button
            key={s.key}
            type="button"
            className={pillClass(filter.source === s.key)}
            aria-pressed={filter.source === s.key}
            onClick={() => update({ source: s.key })}
          >
            {s.label}
          </button>
        ))}
        <span className={FILTER_SEP} aria-hidden="true" />
        <label className={pillClass(filter.highRiskOnly)}>
          <input
            type="checkbox"
            checked={filter.highRiskOnly}
            onChange={(e) => update({ highRiskOnly: e.target.checked })}
          />
          Powerful, high up only
        </label>
        <span className={FILTER_SEP} aria-hidden="true" />
        {(["map", "table"] as const).map((v) => (
          <button
            key={v}
            type="button"
            className={pillClass(view === v)}
            aria-pressed={view === v}
            onClick={() => setView(v)}
          >
            {v === "map" ? "Map" : "Table"}
          </button>
        ))}
      </div>

      {view === "map" && rows.length > 0 && (
        <>
          <AccessMapView rows={rows} />
          <div className={cx(CAPTION, "mt-4 flex flex-wrap items-center gap-1.5")}>
            {(["access", "keys", "change", "data", "view"] as const).map((l) => (
              <span key={l} className={cx(AM_CHIP, LEVEL_CHIP[l], "px-2 py-0 text-[0.75rem]")}>
                {LEVEL_INFO[l].title}
              </span>
            ))}
            <span className="text-quaternary">
              The coloured rail marks everything covered by access that can change who has access or get keys.
            </span>
          </div>
        </>
      )}

      {view === "map" && rows.length > 0 ? null : rows.length === 0 ? (
        <p className={cx("my-[1em]", EMPTY_STATE)}>
          {data.assignments.length === 0
            ? "No current grant gives this identity access, directly or through a group."
            : "No grants match the filters."}
        </p>
      ) : (
        <div className={TABLE_SCROLL}>
          <table className={TABLE_COMPACT}>
            <thead>
              <tr>
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
            {groups.map((g) => (
              <tbody key={g.key || "tenant"}>
                <tr>
                  <th scope="rowgroup" colSpan={6} className="bg-primary_hover! text-left text-[0.8125rem]! font-semibold">
                    {g.key ? <Link to={scopeHref(`/subscriptions/${g.key}`)}>{g.label}</Link> : g.label}
                    <span className="text-quaternary">
                      {" "}
                      · {g.rows.length} grant{g.rows.length === 1 ? "" : "s"}
                      {g.highRisk > 0 && ` · ${g.highRisk} powerful, high up`}
                    </span>
                  </th>
                </tr>
                {g.rows.map((r) => (
                  <tr key={r.assignment_id}>
                    <td className={CELL_ROLE}>
                      <RoleLink roleId={r.role_definition_id} roleName={r.role_name} />{" "}
                      {r.is_high_risk && (
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
                    <td>
                      <span className={cx(BADGE, BADGE_LEVEL[r.scope_level])}>{LEVEL_SHORT[r.scope_level]}</span>{" "}
                      <Link to={scopeHref(r.scope)} title={r.scope}>
                        {scopeLabel(r.scope, r.scope_name)}
                      </Link>
                    </td>
                    <td>
                      {r.source === "direct" ? (
                        <span className={cx(BADGE, SOURCE_TONE.direct)}>Directly</span>
                      ) : (
                        <>
                          <span className={cx(BADGE, SOURCE_TONE.inherited)}>Through</span>{" "}
                          <Link to={principalHref(r.via_group_id ?? "")}>{r.via_group_name ?? r.via_group_id}</Link>
                        </>
                      )}
                    </td>
                    <td className={ORIGIN_CELL}>
                      {originText(r) ?? <span className="text-quaternary">not recorded</span>}
                      {r.grant_id && (
                        <>
                          {" · "}
                          <Link to={grantHref(r.grant_id)}>details</Link>
                        </>
                      )}
                    </td>
                    <td className={r.observed_op_count === 0 ? "text-quaternary" : undefined} title={usageTitle(r)}>
                      {usageLabel(r)}
                      {r.source === "group" && r.observed_op_count ? " (group)" : ""}
                    </td>
                    <td>
                      {r.open_findings > 0 ? (
                        <Link to={principalHref(data.principal.id, "findings")}>{r.open_findings} open</Link>
                      ) : (
                        <span className="text-quaternary">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
      <div className={TABLE_FOOT}>
        <span>
          {rows.length} of {data.assignments.length} grants · access given on a management group reaches every
          subscription below it
        </span>
      </div>

      {data.eligible.length > 0 && (
        <>
          <h3 className={cx(SECTION_TITLE, "mt-6")}>Eligible through PIM ({data.eligible.length})</h3>
          <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
            Not active: roles this identity can switch on when needed, usually with approval or MFA and for a
            limited time.
          </p>
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Can activate</th>
                  <th scope="col">At</th>
                  <th scope="col">Source</th>
                  <th scope="col">Eligible until</th>
                </tr>
              </thead>
              <tbody>
                {data.eligible.map((r) => (
                  <tr key={`${r.role_definition_id}-${r.scope}-${r.via_group_id ?? ""}`}>
                    <td className={CELL_ROLE}>
                      <RoleLink roleId={r.role_definition_id} roleName={r.role_name} />{" "}
                      {r.is_high_risk && (
                        <span className={cx(TAG, TAG_TONE.risk)} title="Powerful role given at management-group or subscription level">
                          R
                        </span>
                      )}{" "}
                      <ConditionTag kind={r.condition_kind} text={r.condition_text} />
                    </td>
                    <td>
                      <span className={cx(BADGE, BADGE_LEVEL[r.scope_level])}>{LEVEL_SHORT[r.scope_level]}</span>{" "}
                      <Link to={scopeHref(r.scope)} title={r.scope}>
                        {scopeLabel(r.scope, r.scope_name)}
                      </Link>
                    </td>
                    <td>
                      {r.source === "direct" ? (
                        <span className={cx(BADGE, SOURCE_TONE.direct)}>Directly</span>
                      ) : (
                        <>
                          <span className={cx(BADGE, SOURCE_TONE.inherited)}>Through</span>{" "}
                          <Link to={principalHref(r.via_group_id ?? "")}>{r.via_group_name ?? r.via_group_id}</Link>
                        </>
                      )}
                    </td>
                    <td>{r.until ? formatDay(r.until) : <span className="text-quaternary">no end date</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </SectionCard>
  );
}
