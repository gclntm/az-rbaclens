import { useEffect, useState } from "react";

import { api, type ActivityEventDetail } from "@/api/client";
import { CAPTION, MONO } from "@/components/classes";
import Drawer from "@/components/Drawer";
import { cx } from "@/components/classes";

/** The event's facts: names on the left, values on one line each. */
const META_DT = "text-[0.8125rem] font-semibold text-tertiary";
const META_DD = "m-0 truncate";
const META_DD_ID = cx(META_DD, MONO, "text-quaternary");

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** One event of the Activity Log in full: who, what, where, and Azure's own record. */
export default function EventDetailDrawer({ eventId, onClose }: { eventId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<ActivityEventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    api.activity
      .getEvent(eventId)
      .then(setDetail)
      .catch((e: Error) => setError(e.message));
  }, [eventId]);

  const title = detail ? (detail.operation_display ?? detail.operation_name) : error ? "Event" : "Loading…";
  return (
    <Drawer title={title} onClose={onClose}>
      {error && <p className="my-[1em] text-error-primary">Could not load the event: {error}</p>}
      {!error && !detail && <p className="my-[1em] text-quaternary">Loading…</p>}
      {detail && (
        <>
          <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <div className="contents">
              <dt className={META_DT}>When</dt>
              <dd className={META_DD}>
                {detail.first_occurred_at
                  ? `${when(detail.first_occurred_at)} to ${when(detail.occurred_at)}`
                  : when(detail.occurred_at)}
              </dd>
            </div>
            {detail.first_occurred_at && (
              <div className="contents">
                <dt className={META_DT}>Times</dt>
                <dd className={META_DD}>{(detail.event_count ?? 1).toLocaleString()}</dd>
              </div>
            )}
            <div className="contents">
              <dt className={META_DT}>Who</dt>
              <dd className={META_DD} title={detail.caller ?? ""}>
                {detail.caller ?? "—"}
                {detail.caller_type && <span className={cx(CAPTION, "text-quaternary")}> ({detail.caller_type})</span>}
              </dd>
            </div>
            <div className="contents">
              <dt className={META_DT}>What</dt>
              <dd className={cx(META_DD, MONO)}>{detail.operation_name}</dd>
            </div>
            <div className="contents">
              <dt className={META_DT}>Result</dt>
              <dd className={META_DD}>{detail.status ?? "—"}</dd>
            </div>
            <div className="contents">
              <dt className={META_DT}>Subscription</dt>
              <dd className={META_DD_ID}>{detail.subscription_id}</dd>
            </div>
            {detail.resource_id && (
              <div className="contents">
                <dt className={META_DT}>Resource</dt>
                <dd className={META_DD_ID} title={detail.resource_id}>
                  {detail.resource_id}
                </dd>
              </div>
            )}
            {detail.resource_type && (
              <div className="contents">
                <dt className={META_DT}>Resource type</dt>
                <dd className={META_DD_ID}>{detail.resource_type}</dd>
              </div>
            )}
            {detail.correlation_id && (
              <div className="contents">
                <dt className={META_DT}>Correlation</dt>
                <dd className={META_DD_ID}>{detail.correlation_id}</dd>
              </div>
            )}
            <div className="contents">
              <dt className={META_DT}>Event id</dt>
              <dd className={META_DD_ID}>{detail.event_data_id}</dd>
            </div>
          </dl>
          <section className="border-t border-t-secondary pt-2">
            <h4 className="mx-0 mt-0 mb-1 text-[0.9rem] font-semibold">Azure&apos;s record</h4>
            {detail.raw == null && detail.first_occurred_at ? (
              <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
                Routine machine activity is kept as one row per day: who, what, where, how many times, first and
                last. Azure&apos;s full record of each event is not kept.
              </p>
            ) : (
              <pre className="m-0 max-h-[50vh] overflow-y-auto rounded-[4px] border border-secondary bg-primary_hover p-2 font-mono text-[0.75rem] leading-[1.5] break-all whitespace-pre-wrap">{JSON.stringify(detail.raw, null, 2)}</pre>
            )}
          </section>
        </>
      )}
    </Drawer>
  );
}
