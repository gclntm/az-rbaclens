import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  api,
  type RoleDefinitionDetail,
  type RoleDefinitionListItem,
  type RoleListSort,
  type RoleUsage,
  type ScopeLevel,
} from "@/api/client";

import { BADGE, BADGE_LEVEL, CAPTION, CARD, MONO, cx } from "@/components/classes";
import { PageHeader, SectionCard, SectionHeader } from "@/components/PageTemplate";
import { principalHref } from "@/pages/principals/principalUtils";
import { scopeHref } from "@/pages/scopes/scopeUtils";

import { groupByScope } from "./roles";

const SCOPE_LEVEL_LABEL: Record<ScopeLevel, string> = {
  directory: "Entra ID",
  mgmt_group: "Mgmt group",
  subscription: "Subscription",
  resource_group: "Resource group",
  resource: "Resource",
};

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

/** The role list beside the detail: a card that stays in view, its header band pinned while the list scrolls. */
const LIST_PANE = cx(
  CARD,
  "sticky top-4 flex max-h-[calc(100vh_-_2_*_var(--space-md))] flex-col gap-2 overflow-y-auto px-0 pt-0 pb-2",
  "[&>header]:sticky [&>header]:top-0 [&>header]:z-1 [&>header]:mb-1",
);
const TOGGLE = "flex cursor-pointer items-center gap-1.5 px-2 pt-0 pb-2 text-[0.8125rem] text-tertiary select-none";
/** One role in the list; the chosen one is tinted, with a blue edge. */
const TREE_ITEM =
  "flex w-full cursor-pointer items-center justify-between gap-1 px-2 py-1 text-left text-[0.875rem] text-primary [border-style:none_none_none_solid] [border-width:0_0_0_2px]";
const TREE_ITEM_STATE = {
  active: "bg-(--accent-blue-subtle) [border-color:currentcolor_currentcolor_currentcolor_var(--accent-blue)]",
  idle: "bg-transparent hover:bg-primary_hover [border-color:currentcolor_currentcolor_currentcolor_transparent]",
} as const;
/** A one-letter flag beside a role's name (C custom, R high risk). */
const ROW_FLAG =
  "ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded-[2px] px-1 align-[1px] text-[0.6875rem] leading-none font-semibold tracking-normal";
const FLAG_TONE = {
  custom: "border border-(--accent-orange) bg-(--accent-orange-subtle) text-warning-primary",
  risk: "bg-(--accent-red) text-(--on-accent)",
  entra: "border border-(--accent-purple) bg-(--accent-purple-subtle) text-(--accent-purple)",
} as const;
const DETAIL_SECTION = "border-t border-t-secondary pt-2";
const DETAIL_SECTION_TITLE = "mx-0 mt-0 mb-1 text-[0.9rem] font-semibold";
/** A name that is cut with an ellipsis rather than wrapped. */
const ONE_LINE = "min-w-0 flex-auto overflow-hidden text-ellipsis whitespace-nowrap";
const ID_SMALL = cx(MONO, "text-quaternary text-[0.6875rem]");

type Filters = {
  q: string;
  role_type: "" | "BuiltInRole" | "CustomRole";
  risk: "" | "high";
  unused: boolean;
  sort: RoleListSort;
};

function filtersFromParams(p: URLSearchParams): Filters {
  return {
    q: p.get("q") ?? "",
    role_type: (p.get("role_type") as Filters["role_type"]) || "",
    risk: (p.get("risk") as Filters["risk"]) || "",
    unused: p.get("unused") === "1",
    sort: (p.get("sort") as RoleListSort) || "name",
  };
}

function filtersToParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams();
  if (f.q) sp.set("q", f.q);
  if (f.role_type) sp.set("role_type", f.role_type);
  if (f.risk) sp.set("risk", f.risk);
  if (f.unused) sp.set("unused", "1");
  if (f.sort && f.sort !== "name") sp.set("sort", f.sort);
  return sp;
}

export default function RolesPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params), [params]);
  const [items, setItems] = useState<RoleDefinitionListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeRoleId = params.get("role");

  function updateFilter(patch: Partial<Filters>) {
    const next = { ...filters, ...patch };
    const sp = filtersToParams(next);
    if (activeRoleId) sp.set("role", activeRoleId);
    setParams(sp, { replace: true });
  }

  function pickRole(roleId: string) {
    const sp = filtersToParams(filters);
    sp.set("role", roleId);
    setParams(sp, { replace: true });
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.iam
      .listRoleDefinitions({
        q: filters.q || undefined,
        role_type: filters.role_type || undefined,
        risk: filters.risk || undefined,
        unused: filters.unused || undefined,
        sort: filters.sort,
      })
      .then(setItems)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filters.q, filters.role_type, filters.risk, filters.unused, filters.sort]);

  // Auto-select the first role when the list changes and either nothing
  // is selected or the prior selection has been filtered out.
  useEffect(() => {
    if (items.length === 0) return;
    if (activeRoleId && items.some((r) => r.role_definition_id === activeRoleId)) {
      return;
    }
    const sp = filtersToParams(filters);
    sp.set("role", items[0].role_definition_id);
    setParams(sp, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  return (
    <>
      <PageHeader
        title="Role guide"
        sub="Every role in the tenant, built-in or custom: what it allows, how risky it is, and who holds it."
      />
      <div className="grid grid-cols-[320px_minmax(0,1fr)] gap-4 [align-items:start] max-[1024px]:grid-cols-[1fr]">
        <aside className={LIST_PANE}>
          <SectionHeader icon="roles" title={`Roles (${items.length})`} hint="Pick one to see it on the right." />
          <input
            type="search"
            className="font-normal tracking-normal mx-2 mt-0 mb-2 block w-[calc(100%_-_2_*_var(--space-sm))]"
            placeholder="Search name or description…"
            value={filters.q}
            onChange={(e) => updateFilter({ q: e.target.value })}
          />
          <div className="flex flex-wrap gap-2">
            <select
              className="font-normal tracking-normal min-w-0 flex-[1_1_0]"
              value={filters.role_type}
              onChange={(e) =>
                updateFilter({
                  role_type: e.target.value as Filters["role_type"],
                })
              }
              title="Filter by role type"
            >
              <option value="">All types</option>
              <option value="BuiltInRole">Built-in</option>
              <option value="CustomRole">Custom</option>
            </select>
            <select
              className="font-normal tracking-normal min-w-0 flex-[1_1_0]"
              value={filters.sort}
              onChange={(e) =>
                updateFilter({ sort: e.target.value as RoleListSort })
              }
              title="Sort order"
            >
              <option value="name">Sort: name</option>
              <option value="usage">Sort: usage</option>
              <option value="risk">Sort: risk first</option>
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            <label className={TOGGLE}>
              <input
                type="checkbox"
                className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] m-0"
                checked={filters.risk === "high"}
                onChange={(e) =>
                  updateFilter({ risk: e.target.checked ? "high" : "" })
                }
              />
              <span>High-risk only</span>
            </label>
            <label className={TOGGLE}>
              <input
                type="checkbox"
                className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] m-0"
                checked={filters.unused}
                onChange={(e) => updateFilter({ unused: e.target.checked })}
              />
              <span>Unused only (cleanup)</span>
            </label>
          </div>
          {error && <p className="my-[1em] text-error-primary">{error}</p>}
          {loading && items.length === 0 && (
            <p className="my-[1em] text-quaternary">Loading roles…</p>
          )}
          {!loading && items.length === 0 && !error && (
            <p className="my-[1em] text-quaternary">
              No roles yet. They appear after the first daily check.
            </p>
          )}
          <ul className="m-0 list-none p-0">
            {items.map((r) => {
              const isActive = r.role_definition_id === activeRoleId;
              return (
                <li key={r.role_definition_id}>
                  <button
                    type="button"
                    className={cx("h-6 leading-[normal] font-normal tracking-normal", TREE_ITEM, isActive ? TREE_ITEM_STATE.active : TREE_ITEM_STATE.idle)}
                    onClick={() => pickRole(r.role_definition_id)}
                    title={r.description ?? ""}
                  >
                    <span className={cx(ONE_LINE, "h-4")}>
                      {r.role_name ?? r.role_definition_id}
                    </span>
                    <span className="flex flex-[0_0_60px] items-center justify-end gap-[3px]">
                      {(r.role_type ?? "").startsWith("Entra") && (
                        <span className={cx(ROW_FLAG, FLAG_TONE.entra)} title="Entra role">
                          E
                        </span>
                      )}
                      {(r.role_type ?? "").endsWith("CustomRole") && (
                        <span className={cx(ROW_FLAG, FLAG_TONE.custom)} title="Custom role">
                          C
                        </span>
                      )}
                      {r.role_risk === "high" && (
                        <span className={cx(ROW_FLAG, FLAG_TONE.risk)} title="High-risk role">
                          R
                        </span>
                      )}
                    </span>
                    <span
                      className="h-[15px] flex-[0_0_32px] text-right text-[0.8125rem] text-quaternary tabular-nums"
                      title={
                        r.assignment_count === 0
                          ? "Not given to anyone right now"
                          : `${r.assignment_count} ${r.assignment_count === 1 ? "grant" : "grants"}`
                      }
                    >
                      {r.assignment_count === 0 ? (
                        <span className="text-quaternary">unused</span>
                      ) : (
                        r.assignment_count
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        <main className="min-w-0">
          {activeRoleId ? (
            <RoleDetail roleId={activeRoleId} />
          ) : (
            <div className={CARD}>
              <p className="my-[1em] text-quaternary">Pick a role on the left.</p>
            </div>
          )}
        </main>
      </div>
    </>
  );
}

function RoleDetail({ roleId }: { roleId: string }) {
  const [detail, setDetail] = useState<RoleDefinitionDetail | null>(null);
  const [usage, setUsage] = useState<RoleUsage | null>(null);
  // Who holds a role across the tenant is for admins; others still get the definition.
  const [usageHidden, setUsageHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setUsage(null);
    setUsageHidden(false);
    setError(null);
    api.iam
      .getRoleDefinition(roleId)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
    api.iam
      .getRoleUsage(roleId)
      .then(setUsage)
      .catch(() => setUsageHidden(true));
  }, [roleId]);

  if (error) {
    return (
      <article className={CARD}>
        <p className="my-[1em] text-error-primary">{error}</p>
      </article>
    );
  }
  if (!detail || (!usage && !usageHidden)) {
    return (
      <article className={CARD}>
        <p className="my-[1em] text-quaternary">Loading…</p>
      </article>
    );
  }

  const learnMoreHref =
    detail.role_type === "BuiltInRole"
      ? "https://learn.microsoft.com/azure/role-based-access-control/built-in-roles"
      : null;

  return (
    <SectionCard
      icon="roles"
      tone={detail.role_risk === "high" ? "danger" : "info"}
      title={detail.role_name ?? detail.role_definition_id}
      hint={
        <>
          {(detail.role_type ?? "").endsWith("CustomRole") ? "Custom role" : "Built-in role"}
          {usage &&
            ` · ${usage.assignment_count} ${usage.assignment_count === 1 ? "grant" : "grants"}, ${usage.principal_count} ${usage.principal_count === 1 ? "person or app" : "people & apps"}, ${usage.scope_count} ${usage.scope_count === 1 ? "place" : "places"}`}
          <span className={cx(MONO, "mx-0 mt-0 mb-2 overflow-hidden text-[0.75rem] text-ellipsis whitespace-nowrap")}> · {detail.role_definition_id}</span>
        </>
      }
    >

      {detail.role_risk === "high" && (
        <div className="flex items-start gap-2 rounded-[4px] bg-(--accent-red-subtle) px-4 py-2 text-[0.8125rem] leading-[1.4] text-primary">
          <span className={cx(ROW_FLAG, FLAG_TONE.risk)}>R</span>
          <div>
            <strong>High-risk role.</strong>
            {detail.risk_reasons.length > 0 ? (
              <ul className="mx-0 mt-1 mb-0 list-disc pl-4 text-[0.8125rem] leading-[1.5]">
                {detail.risk_reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : (
              <p className="my-[1em]">
                Combined with a wide scope (management group / subscription),
                this role warrants extra scrutiny.
              </p>
            )}
          </div>
        </div>
      )}

      {detail.description && (
        <p className="m-0 text-[0.875rem] text-tertiary">{detail.description}</p>
      )}

      <RoleActions permissions={detail.permissions} />

      {detail.assignable_scopes && detail.assignable_scopes.length > 0 && (
        <section className={DETAIL_SECTION}>
          <h4 className={DETAIL_SECTION_TITLE}>Can be given on</h4>
          <ul className={cx(MONO, "m-0 list-disc pl-4 text-[0.8125rem]")}>
            {detail.assignable_scopes.map((s) => (
              <li key={s} className="break-all">{s}</li>
            ))}
          </ul>
        </section>
      )}

      {usage && <RoleUsageSection usage={usage} />}

      {learnMoreHref && (
        <p className={cx("my-[1em]", CAPTION)}>
          <a href={learnMoreHref} target="_blank" rel="noreferrer">
            Microsoft built-in role reference ↗
          </a>
        </p>
      )}
    </SectionCard>
  );
}

function RoleActions({
  permissions,
}: {
  permissions: RoleDefinitionDetail["permissions"];
}) {
  if (!permissions || permissions.length === 0) {
    return (
      <section className={DETAIL_SECTION}>
        <h4 className={DETAIL_SECTION_TITLE}>Permissions</h4>
        <p className="my-[1em] text-quaternary">No actions on file for this role.</p>
      </section>
    );
  }
  return (
    <section className={DETAIL_SECTION}>
      <h4 className={DETAIL_SECTION_TITLE}>Permissions</h4>
      {permissions.map((p, i) => (
        <div key={i} className="flex flex-col gap-2">
          <ActionList title="Actions" actions={p.actions} />
          <ActionList title="NotActions" actions={p.notActions} muted />
          <ActionList title="DataActions" actions={p.dataActions} />
          <ActionList
            title="NotDataActions"
            actions={p.notDataActions}
            muted
          />
        </div>
      ))}
    </section>
  );
}

function ActionList({
  title,
  actions,
  muted,
}: {
  title: string;
  actions: string[];
  muted?: boolean;
}) {
  if (actions.length === 0) return null;
  return (
    <div>
      <h5
        className={cx("mx-0 mt-0 mb-1 text-[0.8125rem] font-semibold", muted && "text-quaternary")}
      >
        {title} <span className={CAPTION_MUTED}>({actions.length})</span>
      </h5>
      <ul className={cx(MONO, "m-0 list-disc pl-4 text-[0.8125rem] leading-[1.5]")}>
        {actions.map((a) => (
          <li key={a} className="break-all">{a}</li>
        ))}
      </ul>
    </div>
  );
}

function RoleUsageSection({ usage }: { usage: RoleUsage }) {
  const groups = useMemo(
    () => groupByScope(usage.assignments),
    [usage.assignments],
  );

  if (usage.assignments.length === 0) {
    return (
      <section className={DETAIL_SECTION}>
        <h4 className={DETAIL_SECTION_TITLE}>Usage</h4>
        <p className="my-[1em] text-quaternary">
          Nobody holds this role right now.
        </p>
      </section>
    );
  }

  return (
    <section className={DETAIL_SECTION}>
      <h4 className={DETAIL_SECTION_TITLE}>
        Given on <span className={CAPTION_MUTED}>({groups.length} place{groups.length === 1 ? "" : "s"})</span>
      </h4>
      <ul className="m-0 flex list-none flex-col gap-4 p-0">
        {groups.map((g) => (
          <li key={g.scope} className="border-l-2 border-l-secondary pl-2">
            <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span
                className={cx(BADGE, BADGE_LEVEL[g.scope_level])}
                title={SCOPE_LEVEL_LABEL[g.scope_level]}
              >
                {SCOPE_LEVEL_LABEL[g.scope_level]}
              </span>
              <span className={ONE_LINE} title={g.scope}>
                {g.scope_name ?? g.scope}
              </span>
              <span className={CAPTION_MUTED}>
                {g.principals.length} {g.principals.length === 1 ? "identity" : "identities"}
              </span>
              <Link
                to={scopeHref(g.scope, "access", usage.role_name)}
                className={CAPTION}
                title={
                  g.latest_scan_finished_at
                    ? `From daily check #${g.latest_scan_id}, finished ${new Date(g.latest_scan_finished_at).toLocaleString()}`
                    : `From daily check #${g.latest_scan_id}`
                }
              >
                see the grants
              </Link>
              {g.scope_name && (
                <span className={ID_SMALL}>
                  {g.scope}
                </span>
              )}
            </div>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {g.principals.map((p) => (
                <li key={p.principal_id} className="flex items-center gap-1 text-[0.8125rem]">
                  <span className={CAPTION_MUTED}>{p.principal_type}</span>
                  <Link
                    to={principalHref(p.principal_id)}
                    className={cx(ONE_LINE, "text-inherit no-underline [border-bottom:1px_dashed_transparent] [transition:color_80ms_ease,border-bottom-color_80ms_ease] hover:text-(--accent-blue) hover:[border-bottom-color:var(--accent-blue)]")}
                    title={p.principal_id}
                  >
                    {p.principal_name ?? p.principal_id}
                  </Link>
                  {p.principal_name && (
                    <span
                      className={ID_SMALL}
                      title={p.principal_id}
                    >
                      {p.principal_id}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}
