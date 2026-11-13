import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import Button from "@/components/Button";
import { CAPTION, CARD, EMPTY_STATE, SEV_DOT, SEV_DOT_TONE } from "@/components/classes";
import {
  api,
  type AttentionItem,
  type ChangesResponse,
  type Overview,
  type ScopeNode,
  type StaleWarning,
  type TodoSummary,
} from "@/api/client";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import ScopeTree from "@/components/ScopeTree";
import { useToast } from "@/components/Toast";
import { isRestricted, placesText, useMe } from "@/hooks/useMe";
import ChangesFeed from "@/pages/changes/ChangesFeed";

import MyHome from "./MyHome";
import { agoText, grantHref } from "@/pages/grants/grantUtils";
import { principalHref } from "@/pages/principals/principalUtils";
import { CATEGORY_SENTENCE, formatDateTime, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";

/** A list of rows with a line between them (Home's "needs a look", reviews, places). */
const FEED = "m-0 list-none p-0";
const FEED_ITEM = "flex flex-wrap items-start gap-4 border-t border-t-secondary py-4 first:border-t-0";
const FEED_MAIN = "flex min-w-0 flex-[1_1_240px] flex-col gap-0.5";
const FEED_TITLE = "font-medium [overflow-wrap:anywhere]";
const FEED_META = "text-[0.75rem] text-tertiary [overflow-wrap:anywhere]";
const FEED_ACTIONS = "flex flex-[0_1_auto] flex-wrap gap-1";
/** Two blocks side by side, one under the other when narrow. */
const TWO_COL = "flex flex-wrap items-start gap-6 [&>*]:min-w-0 [&>*]:flex-[1_1_420px] [&>section]:mb-0";
/** One plain-words sentence under the title. */
const SENTENCE = "m-0 text-[1rem] leading-[1.6]";
/** A block that is the page's first <div>: left out on paper (it says "loading"). */
const PRINT_SKIP_IF_FIRST = "print:[.page-content>&:first-of-type]:hidden";
/** Something to act on, above the blocks. */
const BANNER_WARN =
  "flex flex-wrap items-center justify-between gap-4 rounded-[8px] border border-(--accent-orange) bg-(--accent-orange-subtle) px-6 py-4 text-[0.875rem]";

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

function NeedsLookRow({ item }: { item: AttentionItem }) {
  const who = item.principal_name ?? item.principal_id;
  const where = scopeLabel(item.scope, item.scope_name);
  return (
    <li className={FEED_ITEM}>
      <span className={cx(SEV_DOT, SEV_DOT_TONE[item.severity], "mt-[7px]")} title={`${item.severity} priority`} />
      <div className={FEED_MAIN}>
        <span className={FEED_TITLE}>
          <Link to={principalHref(item.principal_id)}>{who}</Link>
          {item.role_name && <> · {item.role_name}</>} on <Link to={scopeHref(item.scope)}>{where}</Link>
        </span>
        <span className={FEED_META}>{CATEGORY_SENTENCE[item.category] ?? item.detail}</span>
      </div>
      <div className={FEED_ACTIONS}>
        <Button
          size="sm"
          to={item.grant_id ? grantHref(item.grant_id) : scopeHref(item.scope, "findings")}
        >
          Look
        </Button>
      </div>
    </li>
  );
}

/** Home: the whole tenant for admins, or the person's own places for everyone else. */
export default function OverviewPage() {
  const me = useMe();
  if (!me) return <div className={cx(CARD, "text-quaternary", PRINT_SKIP_IF_FIRST)}>Loading…</div>;
  return isRestricted(me) ? <MyHome me={me} /> : <TenantHome />;
}

/**
 * Home: who can do what, what changed, what needs a look. Numbers are for
 * the whole tenant, from the latest daily checks (GET /dashboard/overview).
 */
function TenantHome() {
  const { showToast } = useToast();
  const [data, setData] = useState<Overview | null>(null);
  const [tree, setTree] = useState<ScopeNode[] | null>(null);
  const [changes, setChanges] = useState<ChangesResponse | null>(null);
  const [todos, setTodos] = useState<TodoSummary | null>(null);
  const [stale, setStale] = useState<StaleWarning[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [computing, setComputing] = useState(false);
  const me = useMe();

  const load = useCallback(() => {
    Promise.all([api.overview.get(), api.scopes.tree()])
      .then(([o, t]) => {
        setData(o);
        setTree(t);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    api.changes
      .list({ days: 30, limit: 1 })
      .then(setChanges)
      .catch(() => setChanges(null));
    api.todos
      .summary()
      .then(setTodos)
      .catch(() => setTodos(null));
    api
      .freshness()
      .then((f) => setStale(f.warnings ?? []))
      .catch(() => setStale([]));
  }, []);

  useEffect(load, [load]);

  async function computePending() {
    if (!data) return;
    setComputing(true);
    let failed = 0;
    for (const id of data.findings_pending) {
      try {
        await api.findings.recompute(id);
      } catch {
        failed += 1;
      }
    }
    setComputing(false);
    showToast(failed ? `${failed} could not be worked out` : "Done", failed ? "error" : "success");
    load();
  }

  if (error && !data) {
    return (
      <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
        <p className="my-[1em] text-error-primary">Could not load the home page: {error}</p>
      </div>
    );
  }
  if (!data || !tree) {
    return (
      <div className={cx(CARD, PRINT_SKIP_IF_FIRST)}>
        <p className="my-[1em] text-quaternary">Loading…</p>
      </div>
    );
  }

  const { kpis, freshness } = data;
  const canChangeAccess = kpis.by_level.access ?? 0;

  return (
    <>
      <PageHeader title="Home">
        <div className="inline-flex flex-wrap gap-2 text-[0.8125rem] text-tertiary">
          <span>
            Access as of{" "}
            {data.access_as_of ? formatDateTime(data.access_as_of) : "— no daily check yet"}
          </span>
          <span aria-hidden="true">·</span>
          <span>
            Activity up to {data.activity_up_to ? agoText(data.activity_up_to) : "— nothing collected yet"}
          </span>
        </div>
      </PageHeader>

      {me?.signed_in && me.responsible_for.length > 0 && (
        <p className={SENTENCE}>
          You are responsible for{" "}
          <strong>{placesText(me.responsible_for.map((p) => scopeLabel(p.scope_id, p.name)))}</strong>, and everything
          inside {me.responsible_for.length === 1 ? "it" : "them"}.{" "}
          <Link to="/places">Your places →</Link>
        </p>
      )}

      <p className={SENTENCE}>
        {plural(kpis.principals, "identity", "identities")} hold access across{" "}
        {plural(freshness.subscriptions_total, "subscription")}:{" "}
        <Link to="/people?level=keys">{(kpis.by_level.keys ?? 0).toLocaleString()} can get keys and passwords</Link>,{" "}
        <Link to="/people?level=change">{(kpis.by_level.change ?? 0).toLocaleString()} can change resources</Link>, at
        their strongest, anywhere.
      </p>

      <StatusTiles>
        <StatusTile
          tone={canChangeAccess > 0 ? "warn" : "ok"}
          icon="shield"
          label={LEVEL_INFO.access.title}
          value={plural(canChangeAccess, "identity", "identities")}
          note="Can give anyone any power, themselves included"
          to="/people?level=access"
        />
        <StatusTile
          tone={kpis.needs_look > 0 ? "warn" : "ok"}
          icon="look"
          label="Needs a look"
          value={kpis.needs_look > 0 ? `${kpis.needs_look.toLocaleString()} open` : "Nothing open"}
          note={kpis.unused > 0 ? `Open reasons, not counting ${kpis.unused.toLocaleString()} "no change in 90 days"` : "Open reasons on current grants"}
          to="/findings"
        />
        <StatusTile
          tone="info"
          icon="changes"
          label="Changes, last 30 days"
          value={changes ? `+${changes.given} / −${changes.removed}` : `+${kpis.added_30d} / −${kpis.removed_30d}`}
          note="Access given / removed"
          to="/changes"
        />
      </StatusTiles>

      {stale.map((w) => (
        <div key={w.kind} className={BANNER_WARN} role="alert">
          <span>{w.text}</span>
          <Button size="sm" to={w.path}>
            Data &amp; schedules
          </Button>
        </div>
      ))}

      {todos && todos.overdue > 0 && (
        <div className={BANNER_WARN} role="status">
          <span>
            {todos.overdue === 1
              ? "1 change decided in a review is"
              : `${todos.overdue.toLocaleString()} changes decided in reviews are`}{" "}
            still to make in Azure, more than 14 days later.
          </span>
          <Button size="sm" to="/todos?show=all">
            To do
          </Button>
        </div>
      )}

      {data.findings_pending.length > 0 && (
        <div className={BANNER_WARN} role="status">
          <span>
            What needs a look has not been worked out yet for part of the tenant, so the list below is short.
          </span>
          <Button
            size="sm"
            type="button"
            onClick={computePending}
            disabled={computing}
          >
            {computing ? "Working…" : "Work it out now"}
          </Button>
        </div>
      )}

      <section className={TWO_COL}>
        <SectionCard
          icon="look"
          tone={data.attention.length ? "warn" : "ok"}
          title="Needs a look"
          hint="The most important open reasons, worst first."
          actions={
            <Link to="/findings" className={CAPTION}>
              Full list →
            </Link>
          }
        >
          {data.attention.length === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>Nothing needs a look right now.</p>
          ) : (
            <ul className={FEED}>
              {data.attention.map((a) => (
                <NeedsLookRow key={a.finding_id} item={a} />
              ))}
            </ul>
          )}
          {kpis.unused > 0 && (
            <p className={cx("mb-[1em]", CAPTION, "text-quaternary")} style={{ marginTop: "var(--space-md)" }}>
              Not listed: {plural(kpis.unused, "grant")} with no change in 90 days. Azure never records reads, so
              that alone is not a problem. Each grant page shows it.
            </p>
          )}
        </SectionCard>

        <SectionCard
          icon="changes"
          title="Latest changes"
          hint="Access given or removed, newest first."
          actions={
            <Link to="/changes" className={CAPTION}>
              All changes →
            </Link>
          }
        >
          <ChangesFeed chart filters={false} paging={false} limit={6} emptyText="No change to access in the last 30 days." />
        </SectionCard>
      </section>

      <SectionCard
        icon="places"
        tone={freshness.stale.length ? "warn" : "info"}
        title="Places"
        hint="Management groups, subscriptions and what is inside them, with what needs a look in each."
        actions={
          <Link to="/places" className={CAPTION}>
            All places →
          </Link>
        }
      >
        <ScopeTree nodes={tree} initialDepth={2} />
        {freshness.stale.length > 0 && (
          <p className={cx("mb-[1em]", CAPTION, "text-quaternary")} style={{ marginTop: "var(--space-md)" }}>
            Not checked in the last {freshness.window_days} days:{" "}
            {freshness.stale.map((s, i) => (
              <span key={s.id}>
                {i > 0 && ", "}
                <Link to={scopeHref(s.id)}>{scopeLabel(s.id, s.name)}</Link>{" "}
                ({s.age_days === null ? "never" : `${s.age_days} days`})
              </span>
            ))}
          </p>
        )}
      </SectionCard>
    </>
  );
}
