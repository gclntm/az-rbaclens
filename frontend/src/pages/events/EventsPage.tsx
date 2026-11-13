import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import Button from "@/components/Button";
import {
  CAPTION,
  CELL_STACK,
  CELL_SUB,
  EMPTY_STATE,
  FORM_ROW,
  MONO,
  TABLE_COMPACT,
  TABLE_ROW_CLICKABLE,
  TABLE_SCROLL,
} from "@/components/classes";
import {
  api,
  type ActivityEvent,
  type ActivityEventFilters,
  type ActivityPull,
  dailyCountText,
  MGMT_GROUPS,
  MGMT_GROUPS_NAME,
  type SubscriptionNode,
} from "@/api/client";
import DownloadLink from "@/components/DownloadLink";
import { PageHeader, SectionCard, StatePill, type Tone } from "@/components/PageTemplate";
import { useActivePolling } from "@/hooks/useActivePolling";
import { formatDateTime } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

import EventDetailDrawer from "./EventDetailDrawer";

const POLL_MS = 5000;
const LIMIT = 500;
const RESULTS = ["Succeeded", "Failed", "Started", "Accepted"];

function resultTone(status: string | null): Tone | "none" {
  if (status === "Failed") return "danger";
  if (status === "Succeeded") return "ok";
  return status ? "info" : "none";
}

/** ".../resourceGroups/rg/providers/x/y/name" → "…/y/name". */
export function shortenResource(id: string): string {
  const parts = id.replace(/\/$/, "").split("/").filter(Boolean);
  return parts.length <= 2 ? id : `…/${parts.slice(-2).join("/")}`;
}

/**
 * The Activity Log as collected: changes made in Azure, newest first, with
 * filters on where, who, what and the result. Collections themselves run from
 * Data & schedules; while one runs, the list reloads when it ends.
 */
export default function EventsPage() {
  const [params] = useSearchParams();
  const [collections, setCollections] = useState<ActivityPull[]>([]);
  const [subscriptions, setSubscriptions] = useState<SubscriptionNode[]>([]);

  async function refresh() {
    try {
      setCollections(await api.activity.listPulls(undefined, 20));
    } catch {
      // Only the line under the title depends on it; the events still load.
    }
  }

  useEffect(() => {
    void refresh();
    api.scopeBrowser
      .subscriptions()
      .then(setSubscriptions)
      .catch(() => setSubscriptions([]));
  }, []);

  const running = collections.filter((p) => p.status === "pending" || p.status === "running").length;
  useActivePolling(refresh, POLL_MS, running > 0);

  // A collection just ended: reload the events.
  const [reloadKey, setReloadKey] = useState(0);
  const wasRunning = useRef(running > 0);
  useEffect(() => {
    if (wasRunning.current && running === 0) setReloadKey((k) => k + 1);
    wasRunning.current = running > 0;
  }, [running]);

  const lastDone = collections.find((p) => p.status === "done");
  return (
    <>
      <PageHeader
        title="Activity Log"
        sub={
          <>
            Changes made in Azure, collected from the Activity Log of each subscription and of the management groups.
            Reads are never recorded.{" "}
            {running > 0
              ? `${running === 1 ? "A collection is" : `${running} collections are`} running: the list reloads when ${running === 1 ? "it ends" : "they end"}.`
              : lastDone
                ? `Last collection ended ${formatDateTime(lastDone.finished_at)}.`
                : "Nothing collected yet."}
          </>
        }
        actions={
          <Button size="sm" to="/admin/data">
            Data &amp; schedules
          </Button>
        }
      />

      <EventsSection
        initialSubscription={params.get("sub") || undefined}
        initialCaller={params.get("caller") || undefined}
        initialOperation={params.get("operation") || undefined}
        subscriptions={subscriptions}
        reloadKey={reloadKey}
      />
    </>
  );
}

function EventsSection({
  initialSubscription,
  initialCaller,
  initialOperation,
  subscriptions,
  reloadKey,
}: {
  initialSubscription?: string;
  initialCaller?: string;
  initialOperation?: string;
  subscriptions: SubscriptionNode[];
  reloadKey: number;
}) {
  const names = useMemo(
    () => new Map([...subscriptions.map((s) => [s.id.toLowerCase(), s.display_name] as const), [MGMT_GROUPS, MGMT_GROUPS_NAME]]),
    [subscriptions],
  );
  const [filters, setFilters] = useState<ActivityEventFilters>({
    subscription_id: initialSubscription,
    caller: initialCaller,
    operation_name: initialOperation,
    limit: LIMIT,
  });
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  useEffect(() => {
    setFilters((f) => (f.subscription_id === initialSubscription ? f : { ...f, subscription_id: initialSubscription }));
  }, [initialSubscription]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api.activity
      .listEvents(filters)
      .then((rows) => !cancelled && setEvents(rows))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [filters, reloadKey]);

  const set = (patch: Partial<ActivityEventFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const filtered = !!(filters.subscription_id || filters.caller || filters.operation_name || filters.status);
  // The API matches the id exactly: offer each subscription's id as Azure spells it.
  const sub = filters.subscription_id
    ? (subscriptions.find((s) => s.id.toLowerCase() === filters.subscription_id?.toLowerCase())?.id ?? filters.subscription_id)
    : "";
  const n = events?.length ?? 0;

  return (
    <SectionCard
      icon="changes"
      title={events === null ? "Events" : `Events (${n === LIMIT ? `newest ${LIMIT}` : n})`}
      hint="Newest first. Open a row for the full event."
      actions={
        <>
          <DownloadLink href={api.activity.exportEventsUrl("csv", filters)} title={filtered ? "These events, as CSV" : "All events, as CSV"}>
            CSV
          </DownloadLink>
          <DownloadLink href={api.activity.exportEventsUrl("json", filters)} title={filtered ? "These events, as JSON" : "All events, as JSON"}>
            JSON
          </DownloadLink>
        </>
      }
    >
      <div className={FORM_ROW}>
        <select className="font-normal tracking-normal" aria-label="Where" value={sub} onChange={(e) => set({ subscription_id: e.target.value || undefined })}>
          <option value="">Everywhere</option>
          <option value={MGMT_GROUPS}>{MGMT_GROUPS_NAME}</option>
          {[...subscriptions]
            .sort((a, b) => a.display_name.localeCompare(b.display_name))
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.display_name}
              </option>
            ))}
          {sub && !names.has(sub.toLowerCase()) && <option value={sub}>{sub}</option>}
        </select>
        <input
          className="font-normal tracking-normal"
          type="search"
          aria-label="Who"
          placeholder="Who (part of a name or id)"
          value={filters.caller ?? ""}
          onChange={(e) => set({ caller: e.target.value || undefined })}
        />
        <input
          className="font-normal tracking-normal"
          type="search"
          aria-label="What"
          placeholder="What (part of an operation)"
          value={filters.operation_name ?? ""}
          onChange={(e) => set({ operation_name: e.target.value || undefined })}
        />
        <select className="font-normal tracking-normal" aria-label="Result" value={filters.status ?? ""} onChange={(e) => set({ status: e.target.value || undefined })}>
          <option value="">Any result</option>
          {RESULTS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {error && <p className="my-[1em] text-error-primary">Could not load the events: {error}</p>}
      {!error && events === null && <p className="my-[1em] text-quaternary">Loading…</p>}
      {!error && events?.length === 0 && (
        <p className={cx("my-[1em]", EMPTY_STATE)}>
          {filtered ? "No event matches these filters." : "No event collected yet. Collections run from Data & schedules."}
        </p>
      )}
      {!!events?.length && (
        <div className={TABLE_SCROLL}>
          <table className={TABLE_COMPACT}>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Where</th>
                <th scope="col">Who</th>
                <th scope="col">What</th>
                <th scope="col">Resource</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr
                  key={e.id}
                  className={TABLE_ROW_CLICKABLE}
                  tabIndex={0}
                  aria-label={`${e.operation_display ?? e.operation_name} by ${e.caller ?? "unknown"}: open the event`}
                  onClick={() => setSelected(e.id)}
                  onKeyDown={(k) => {
                    if (k.key === "Enter" || k.key === " ") {
                      k.preventDefault();
                      setSelected(e.id);
                    }
                  }}
                >
                  <td title={e.occurred_at}>
                    {formatDateTime(e.occurred_at)}
                    {dailyCountText(e) && (
                      <div className={cx(CAPTION, "text-quaternary")} title="Routine machine activity, kept as one row per day">
                        {dailyCountText(e)}
                      </div>
                    )}
                  </td>
                  <td title={e.subscription_id}>
                    {names.get(e.subscription_id.toLowerCase()) ?? <span className={MONO}>{e.subscription_id.slice(0, 8)}…</span>}
                  </td>
                  <td>
                    <div className={CELL_STACK}>
                      <span title={e.caller ?? ""}>{e.caller ?? "—"}</span>
                      {e.caller_type && <span className={CELL_SUB}>{e.caller_type}</span>}
                    </div>
                  </td>
                  <td title={e.operation_name}>
                    {e.operation_display ?? e.operation_name}
                    {e.operation_display && <div className={cx(CAPTION, "text-quaternary", MONO)}>{e.operation_name}</div>}
                  </td>
                  <td className={cx(MONO, CAPTION)} title={e.resource_id ?? ""}>
                    {e.resource_id ? shortenResource(e.resource_id) : "—"}
                  </td>
                  <td>
                    <StatePill tone={resultTone(e.status)}>{e.status ?? "—"}</StatePill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {selected !== null && <EventDetailDrawer eventId={selected} onClose={() => setSelected(null)} />}
    </SectionCard>
  );
}
