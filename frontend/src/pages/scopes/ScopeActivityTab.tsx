import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, type ScopeActivity } from "@/api/client";
import { SectionCard } from "@/components/PageTemplate";
import {
  BADGE,
  BADGE_TONE,
  CAPTION,
  CARD,
  EMPTY_STATE,
  MONO,
  NUM,
  TABLE_COMPACT,
  TABLE_SCROLL,
} from "@/components/classes";
import { cx } from "@/components/classes";

import {
  ageLabel,
  formatDateTime,
  freshnessOf,
  principalLabel,
  scopeHref,
  scopeLabel,
  SOURCE_LABEL,
} from "./scopeUtils";

/** Where a grant comes from, as a badge colour. */
const BADGE_SOURCE: Record<string, string> = {
  direct: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  inherited: "bg-(--accent-purple-subtle) text-(--accent-purple)",
  below: "bg-primary_hover text-tertiary",
};
/** How long ago a subscription's log was collected: the text colour, and its dot. */
const AGE_TEXT: Record<string, string> = {
  fresh: "",
  aging: "text-warning-primary",
  stale: "text-error-primary",
  never: "text-quaternary",
};
const FRESH_DOT = "inline-block size-2 flex-[0_0_8px] rounded-full";
const FRESH_DOT_TONE: Record<string, string> = {
  fresh: "bg-(--accent-green)",
  aging: "bg-(--accent-orange)",
  stale: "bg-(--accent-red)",
  never: "border border-(--text-muted) bg-transparent",
};
/** A list with a line between rows. */
const FEED =
  "m-0 list-none p-0 " +
  "[&>li]:flex [&>li]:flex-wrap [&>li]:items-start [&>li]:gap-4 [&>li]:border-t [&>li]:border-t-(--border) [&>li]:px-0 [&>li]:py-4 " +
  "[&>li:first-child]:[border-top:0]";
const FEED_MAIN = "flex min-w-0 flex-[1_1_240px] flex-col gap-0.5";
const FEED_TITLE = "font-medium [overflow-wrap:anywhere]";
const FEED_META = "text-[0.75rem] text-tertiary [overflow-wrap:anywhere]";
const MUTED_CAPTION = cx(CAPTION, "text-quaternary");

function daysSince(iso: string | null | undefined): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

/**
 * What the Activity Log says about a scope: pull freshness per
 * subscription, the most active assignments that apply here, and callers
 * active in the snapshot's subscriptions with no visible role assignment.
 */
export default function ScopeActivityTab({ scopeId }: { scopeId: string }) {
  const [data, setData] = useState<ScopeActivity | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    api.scopes
      .activity(scopeId)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [scopeId]);

  if (error) return <div className={cx(CARD, "text-error-primary")}>Could not load activity: {error}</div>;
  if (!data) return <div className={cx(CARD, "text-quaternary")}>Loading activity…</div>;

  return (
    <>
      <SectionCard
        icon="changes"
        title="Activity Log by subscription"
        hint="Changes recorded in each subscription inside this place, and when they were collected."
        actions={
          <>
            <Link to="/admin/data" className={CAPTION}>
              Schedules →
            </Link>
          </>
        }
      >
        {data.subscriptions.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>No subscription inside this place is known yet.</p>
        ) : (
          <div className={TABLE_SCROLL}>
            <table className={TABLE_COMPACT}>
              <thead>
                <tr>
                  <th scope="col">Subscription</th>
                  <th scope="col" className={NUM}>Changes · 90 d</th>
                  <th scope="col">Newest change</th>
                  <th scope="col">Last collected</th>
                  <th scope="col">Last try</th>
                </tr>
              </thead>
              <tbody>
                {data.subscriptions.map((s) => {
                  const okAge = daysSince(s.last_successful_pull?.finished_at);
                  const fresh = freshnessOf(okAge, 7);
                  return (
                    <tr key={s.id}>
                      <td>
                        <Link to={scopeHref(s.id)}>{scopeLabel(s.id, s.name)}</Link>
                      </td>
                      <td className={NUM}>{s.events_90d.toLocaleString()}</td>
                      <td>{formatDateTime(s.newest_event_at)}</td>
                      <td>
                        <span className={AGE_TEXT[fresh] || undefined}>
                          <span className={cx(FRESH_DOT, FRESH_DOT_TONE[fresh])} aria-hidden="true" />{" "}
                          {s.last_successful_pull
                            ? `${formatDateTime(s.last_successful_pull.finished_at)} · ${ageLabel(okAge)}`
                            : "never collected"}
                        </span>
                      </td>
                      <td>
                        {s.last_pull ? (
                          <span className={cx(BADGE, BADGE_TONE[s.last_pull.status as keyof typeof BADGE_TONE])}>{s.last_pull.status}</span>
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
      </SectionCard>

      <section className="flex flex-wrap items-start gap-6 [&>*]:mb-0 [&>*]:min-w-0 [&>*]:flex-[1_1_420px]">
        <SectionCard
          icon="people"
          title="Who changed the most here"
          hint="Grants that apply here, by the changes made with them."
          actions={
            <>
              {data.window_days && <span className={MUTED_CAPTION}>last {data.window_days} days</span>}
            </>
          }
        >
          {data.most_active.length === 0 ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>No change recorded by anyone with access here.</p>
          ) : (
            <ul className={FEED}>
              {data.most_active.map((a) => (
                <li key={a.assignment_id}>
                  <span className={cx(BADGE, BADGE_SOURCE[a.source])}>{SOURCE_LABEL[a.source]}</span>
                  <div className={FEED_MAIN}>
                    <span className={FEED_TITLE}>
                      {principalLabel(a)} · {a.role_name ?? "role"}
                    </span>
                    <span className={FEED_META}>
                      {a.event_count.toLocaleString()} changes, {a.observed_op_count} kinds · given
                      on <Link to={scopeHref(a.scope)}>{scopeLabel(a.scope, a.scope_name)}</Link>
                    </span>
                    {a.top_operations.length > 0 && (
                      <span className={FEED_META}>
                        {a.top_operations.map((o) => `${o.display ?? o.operation_name} ×${o.count}`).join(" · ")}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </SectionCard>

        <SectionCard
          icon="shield"
          title="Changes by identities with no grant here"
          hint="They acted through a grant somewhere else, or through a group."
          actions={
            <>
              {data.snapshot_id && <span className={MUTED_CAPTION}>check #{data.snapshot_id}</span>}
            </>
          }
        >
          {data.snapshot_id === null ? (
            <p className={cx("my-[1em]", EMPTY_STATE)}>
              Not worked out yet for this place.
            </p>
          ) : (
            <>
              <p className={cx("mb-[1em]", CAPTION, "text-secondary")} style={{ marginTop: 0 }}>
                {data.events_attributed?.toLocaleString()} of {data.events_considered?.toLocaleString()} changes
                were made by someone with a grant here. The identities below made changes without one: often a
                grant on a place the check did not cover, or an Azure platform identity.
              </p>
              {data.unattributed_callers.length === 0 ? (
                <p className={cx("my-[1em]", EMPTY_STATE)}>Every change was made by someone with a grant here.</p>
              ) : (
                <ul className={FEED}>
                  {data.unattributed_callers.map((c) => (
                    <li key={`${c.caller}-${c.caller_type}`}>
                      <span className={NUM} style={{ minWidth: 56 }}>
                        {c.event_count.toLocaleString()}
                      </span>
                      <div className={FEED_MAIN}>
                        <span className={FEED_TITLE}>{c.caller_name ?? c.caller}</span>
                        <span className={FEED_META}>
                          {c.caller_name && <span className={MONO}>{c.caller} · </span>}
                          {c.caller_type ?? "unknown type"} ·{" "}
                          {c.direct_assignment_count + c.indirect_membership_count > 0 ? (
                            `${c.direct_assignment_count} direct / ${c.indirect_membership_count} through groups in other checks`
                          ) : (
                            <span className="text-error-primary">not seen in any check</span>
                          )}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </SectionCard>
      </section>
    </>
  );
}
