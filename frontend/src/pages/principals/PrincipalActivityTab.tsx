import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { pillClass } from "@/components/chips";
import Button from "@/components/Button";
import {
  BADGE,
  BADGE_TONE,
  CAPTION,
  EMPTY_STATE,
  FILTER_BAR,
  MONO,
  NUM,
  SECTION_TITLE,
  TABLE_COMPACT,
  TABLE_FOOT,
  TABLE_SCROLL,
} from "@/components/classes";
import { api, dailyCountText, type PrincipalActivity, type RegistryPrincipal } from "@/api/client";
import { SectionCard } from "@/components/PageTemplate";
import { formatDateTime, scopeHref } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

const WINDOWS = [7, 30, 90] as const;

/** A row of small figures above the tables. */
const KPI_INLINE =
  "grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-4 border-t border-t-secondary pt-4";
const KPI_STACK = "flex flex-col gap-0.5";
const KPI_LABEL = "text-[0.6875rem] font-semibold tracking-[0.06em] text-tertiary uppercase";
const KPI_VALUE = "text-[1.25rem] leading-[1.1] font-semibold";
const KPI_HINT = "text-[0.75rem] text-tertiary";
const SPLIT_2 = "mx-0 my-4 grid grid-cols-[repeat(auto-fit,minmax(min(320px,100%),1fr))] gap-6";
/** An operation name in one line, cut with an ellipsis. */
const CELL_OP = "max-w-[420px] overflow-hidden text-[0.75rem] text-ellipsis whitespace-nowrap";

/** The principal's own Activity Log events, matched on the JWT object id. */
export default function PrincipalActivityTab({ principal }: { principal: RegistryPrincipal }) {
  const [days, setDays] = useState<number>(90);
  const [data, setData] = useState<PrincipalActivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isGroup = principal.principal_type === "Group";

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    api.principals
      .activity(principal.id, days)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [principal.id, days]);

  const eventsLink = data?.callers[0]
    ? `/events?caller=${encodeURIComponent(data.callers[0])}`
    : "/events";

  return (
    <SectionCard
      icon="changes"
      title="What they did"
      hint={isGroup ? "Groups never call Azure themselves: these are their members' changes." : "Changes they made in Azure, from the Activity Log of each subscription. Reads are never recorded."}
    >
      <div className={FILTER_BAR}>
        {WINDOWS.map((d) => (
          <button
            key={d}
            type="button"
            className={pillClass(days === d)}
            aria-pressed={days === d}
            onClick={() => setDays(d)}
          >
            Last {d} days
          </button>
        ))}
      </div>

      {error && <p className="my-[1em] text-error-primary">{error}</p>}
      {!data && !error && <p className="my-[1em] text-quaternary">Loading activity…</p>}
      {data && data.identities === 0 && (
        <p className={cx("my-[1em]", EMPTY_STATE)}>No members resolved for this group, so no activity can be attributed.</p>
      )}
      {data && data.identities > 0 && data.event_count === 0 && (
        <p className={cx("my-[1em]", EMPTY_STATE)}>
          No change recorded in the last {days} days. Reads are never recorded, and the Activity Log is collected
          only for subscriptions that have a schedule (see <Link to="/admin/data">Data &amp; schedules</Link>).
        </p>
      )}
      {data && data.event_count > 0 && (
        <>
          <div className={KPI_INLINE}>
            <div className={KPI_STACK}>
              <div className={KPI_LABEL}>Changes</div>
              <div className={KPI_VALUE}>{data.event_count.toLocaleString()}</div>
              <div className={KPI_HINT}>
                {isGroup ? `by ${data.identities} members` : `last ${days} days`}
              </div>
            </div>
            <div className={KPI_STACK}>
              <div className={KPI_LABEL}>Kinds of change</div>
              <div className={KPI_VALUE}>{data.operation_count.toLocaleString()}</div>
              <div className={KPI_HINT}>different operations</div>
            </div>
            <div className={cx(KPI_STACK, data.failed_count ? "border-t-(--accent-orange)" : undefined)}>
              <div className={KPI_LABEL}>Failed</div>
              <div className={cx(KPI_VALUE, data.failed_count ? "text-warning-primary" : undefined)}>
                {data.failed_count.toLocaleString()}
              </div>
              <div className={KPI_HINT}>refused or failed in Azure</div>
            </div>
            <div className={KPI_STACK}>
              <div className={KPI_LABEL}>Newest</div>
              <div className={cx(KPI_VALUE, "text-[0.875rem]")}>{formatDateTime(data.newest_at)}</div>
              <div className={KPI_HINT}>
                as {data.callers.slice(0, 2).join(", ") || "—"}
              </div>
            </div>
          </div>

          <div className={SPLIT_2}>
            <div>
              <h3 className={SECTION_TITLE}>What they did most</h3>
              <table className={TABLE_COMPACT}>
                <tbody>
                  {data.top_operations.map((o) => (
                    <tr key={o.operation}>
                      <td className={CELL_OP} title={o.operation}>
                        {o.display ?? o.operation}
                      </td>
                      <td className={NUM}>{o.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3 className={SECTION_TITLE}>By subscription</h3>
              <table className={TABLE_COMPACT}>
                <tbody>
                  {data.by_subscription.map((s) => (
                    <tr key={s.subscription_id}>
                      <td>
                        <Link to={scopeHref(`/subscriptions/${s.subscription_id}`, "activity")}>
                          {s.subscription_name ?? s.subscription_id}
                        </Link>
                      </td>
                      <td className={NUM}>{s.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <h3 className={SECTION_TITLE}>Latest changes</h3>
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">What</th>
                  <th scope="col">Status</th>
                  <th scope="col">Resource</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((e) => (
                  <tr key={e.id}>
                    <td>
                      {formatDateTime(e.occurred_at)}
                      {dailyCountText(e) && <div className={cx(CAPTION, "text-quaternary")}>{dailyCountText(e)}</div>}
                    </td>
                    <td className={CELL_OP} title={e.operation_name}>
                      {e.operation_display ?? e.operation_name}
                    </td>
                    <td>
                      <span className={cx(BADGE, e.status === "Failed" ? BADGE_TONE.failed : BADGE_TONE.done)}>
                        {e.status ?? "—"}
                      </span>
                    </td>
                    <td className={cx(MONO, CELL_OP)} title={e.resource_id ?? ""}>
                      {e.resource_id?.split("/").slice(-2).join("/") ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!isGroup && (
            <div className={TABLE_FOOT}>
              <span>Showing the newest {data.recent.length}</span>
              <Button size="sm" to={eventsLink}>
                All their changes →
              </Button>
            </div>
          )}
        </>
      )}
    </SectionCard>
  );
}
