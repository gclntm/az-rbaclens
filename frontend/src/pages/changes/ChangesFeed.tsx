import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import LazyWeekChart from "@/components/charts/LazyWeekChart";
import { pillClass } from "@/components/chips";
import { EMPTY_STATE, FILTER_BAR, FILTER_SEP, LEVEL_PILL, WHO_ACCENT } from "@/components/classes";
import Button from "@/components/Button";
import { api, type AccessChange, type ChangesParams, type ChangesResponse } from "@/api/client";
import { grantHref } from "@/pages/grants/grantUtils";
import { principalHref } from "@/pages/principals/principalUtils";
import { scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";
import { LEVEL_INFO } from "@/pages/scopes/whoUtils";
import { cx } from "@/components/classes";

import { byDay, changeParts, isGiven, sourceText, timeText } from "./changeUtils";

const PAGE = 50;

/** One plain-words sentence above a list (also on Home). */
const SENTENCE = "m-0 text-[1rem] leading-[1.6]";
const CHANGE_ICON = "inline-flex size-6 items-center justify-center rounded-full font-bold";
const CHANGE_ICON_TONE = {
  given: "bg-(--accent-green-subtle) text-success-primary",
  removed: "bg-(--accent-red-subtle) text-error-primary",
} as const;
const PERIODS = [7, 30, 90];

type Direction = NonNullable<ChangesParams["direction"]>;

/**
 * Who got or lost access, when, and who did it (GET /changes), as a
 * timeline grouped by day. Used on the Changes page, a place's Changes
 * tab, an identity page and Home.
 */
export default function ChangesFeed({
  scope,
  principal,
  initialDays = 30,
  filters = true,
  chart = false,
  summary = false,
  paging = true,
  limit = PAGE,
  emptyText,
}: {
  scope?: string;
  principal?: string;
  initialDays?: number;
  filters?: boolean;
  chart?: boolean;
  /** One sentence with the counts, above the chart. */
  summary?: boolean;
  /** "Show more" at the end; off for a short preview (Home). */
  paging?: boolean;
  limit?: number;
  emptyText?: string;
}) {
  const [days, setDays] = useState(initialDays);
  const [direction, setDirection] = useState<Direction>("all");
  const [powerful, setPowerful] = useState(false);
  const [data, setData] = useState<ChangesResponse | null>(null);
  const [items, setItems] = useState<AccessChange[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const params = useCallback(
    (offset: number): ChangesParams => ({ scope, principal, days, direction, powerful_only: powerful, limit, offset }),
    [scope, principal, days, direction, powerful, limit],
  );

  useEffect(() => {
    let cancelled = false;
    setData(null);
    api.changes
      .list(params(0))
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setItems(d.items);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [params]);

  async function more() {
    setLoadingMore(true);
    try {
      const d = await api.changes.list(params(items.length));
      setItems((prev) => [...prev, ...d.items]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {filters && (
        <div className={FILTER_BAR}>
          {PERIODS.map((d) => (
            <button
              key={d}
              type="button"
              className={pillClass(days === d)}
              aria-pressed={days === d}
              onClick={() => setDays(d)}
            >
              {d} days
            </button>
          ))}
          <span className={FILTER_SEP} aria-hidden="true" />
          {(["all", "given", "removed"] as Direction[]).map((d) => (
            <button
              key={d}
              type="button"
              className={pillClass(direction === d)}
              aria-pressed={direction === d}
              onClick={() => setDirection(d)}
            >
              {d === "all" ? "All changes" : d === "given" ? "Access given" : "Access removed"}
              {data && d !== "all" && <span className="text-tertiary"> {d === "given" ? data.given : data.removed}</span>}
            </button>
          ))}
          <span className={FILTER_SEP} aria-hidden="true" />
          <label className={pillClass(powerful)}>
            <input type="checkbox" checked={powerful} onChange={(e) => setPowerful(e.target.checked)} />
            Only powerful access
          </label>
        </div>
      )}

      {error && <p className="my-[1em] text-error-primary">Could not load the changes: {error}</p>}
      {!data && !error && <p className="my-[1em] text-quaternary">Loading changes…</p>}

      {data && summary && (
        <p className={SENTENCE}>
          <strong>
            {data.given + data.removed} {data.given + data.removed === 1 ? "change" : "changes"} to access in the last{" "}
            {days} days
          </strong>
          : {data.given} given, {data.removed} removed.
          {data.powerful > 0 && (
            <span className="text-error-primary">
              {" "}
              {data.powerful} of them {data.powerful === 1 ? "is" : "are"} about powerful access.
            </span>
          )}
        </p>
      )}

      {data && chart && data.weeks.length > 0 && <LazyWeekChart weeks={data.weeks} />}

      {data && items.length === 0 && (
        <p className={cx("my-[1em]", EMPTY_STATE)}>
          {emptyText ?? `No change to access in the last ${days} days.`}
        </p>
      )}

      {data &&
        byDay(items).map((day) => (
          <section key={day.key}>
            <h3 className="mx-0 mt-0 mb-1 text-[0.8125rem] font-semibold text-tertiary">{day.label}</h3>
            <ul className="m-0 list-none p-0">
              {day.items.map((c) => (
                <ChangeRow key={c.id} change={c} />
              ))}
            </ul>
          </section>
        ))}

      {data && paging && items.length < data.total && (
        <Button size="sm" type="button" onClick={more} disabled={loadingMore}>
          {loadingMore ? "Loading…" : `Show ${Math.min(limit, data.total - items.length)} more`}
        </Button>
      )}
    </div>
  );
}

function ChangeRow({ change: c }: { change: AccessChange }) {
  const given = isGiven(c.kind);
  const p = changeParts(c);
  const who = c.principal_name ?? c.principal_id;
  const where = scopeLabel(c.scope, c.scope_name);
  const role = c.grant_id && c.kind === "granted" ? <Link to={grantHref(c.grant_id)}>{p.role}</Link> : <strong>{p.role}</strong>;
  return (
    <li className="grid grid-cols-[4.75rem_1.5rem_minmax(0,1fr)_auto] items-start gap-2 border-t border-t-secondary py-2 first:border-t-0 max-[640px]:grid-cols-[1.5rem_minmax(0,1fr)]">
      <span className="pt-0.5 text-[0.8125rem] whitespace-nowrap text-tertiary tabular-nums max-[640px]:-order-1 max-[640px]:pt-0 max-[640px]:[grid-column:2]">{timeText(c)}</span>
      <span className={cx(CHANGE_ICON, CHANGE_ICON_TONE[given ? "given" : "removed"])} aria-hidden="true">
        {given ? "+" : "−"}
      </span>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="[overflow-wrap:anywhere]">
          <Link to={principalHref(c.principal_id)}>{who}</Link> {p.verb}{" "}
          {p.group ? (
            <>
              <Link to={principalHref(c.via_group_id ?? "")}>{p.group}</Link>
              {p.bridge} {role}
            </>
          ) : (
            role
          )}{" "}
          on <Link to={scopeHref(c.scope)} title={c.scope}>{where}</Link>
          {c.kind === "eligible_added" && " on request (PIM)"}
        </span>
        <span className="text-[0.8125rem] text-tertiary [overflow-wrap:anywhere]">{sourceText(c)}</span>
      </div>
      <span className={cx(LEVEL_PILL, WHO_ACCENT[c.level], "text-[0.8125rem] whitespace-nowrap max-[640px]:[grid-column:2]")}>{LEVEL_INFO[c.level].title}</span>
    </li>
  );
}
