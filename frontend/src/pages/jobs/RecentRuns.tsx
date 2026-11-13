import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { pillClass } from "@/components/chips";
import Button from "@/components/Button";
import { CAPTION, EMPTY_STATE, FILTER_BAR, TABLE_COMPACT, TABLE_SCROLL, cx } from "@/components/classes";
import { api, type Job } from "@/api/client";
import { SectionCard, StatePill, type Tone } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { useActivePolling } from "@/hooks/useActivePolling";
import { formatDateTime } from "@/pages/scopes/scopeUtils";

import { CELL_TARGET, ErrorText, ROW_ACTIONS } from "./SchedulesTable";
import { durationText, isActive, jobHref, KIND_LABEL, RUN_FILTERS, type RunFilter, targetDisplay } from "./jobUtils";

const ACTIVE_POLL_MS = 2000;
const IDLE_POLL_MS = 30_000;
const LIMIT = 50;
const STATUS_TONE: Record<Job["status"], Tone> = { done: "ok", failed: "danger", running: "info", pending: "info" };
const ORIGIN_LABEL = { schedule: "scheduled", manual: "started by hand", recheck: "second pass" } as const;
const DAY_MS = 86_400_000;

const CAPTION_MUTED = cx(CAPTION, "text-quaternary");

/**
 * Recent checks and collections, newest first, filtered on the server so "Failed" shows the
 * newest failures however many runs succeeded since. The filter lives in ``?runs=``.
 * ``reloadKey`` changes when the page started or deleted something.
 */
export default function RecentRuns({ names, reloadKey }: { names: ReadonlyMap<string, string>; reloadKey: number }) {
  const { showToast } = useToast();
  const [sp, setSp] = useSearchParams();
  const requested = sp.get("runs") as RunFilter | null;
  const filter = RUN_FILTERS.find((f) => f.key === requested) ?? RUN_FILTERS[0];
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Failures in the last 24 hours, shown on the "Failed" filter whatever is listed.
  const [failedToday, setFailedToday] = useState(0);
  // A filter was picked and its rows are on the way. The rows on screen stay until then:
  // emptying the table would shorten the page and make it jump up under the reader.
  const [switching, setSwitching] = useState(false);
  // Only the newest request may fill the table (an older one can answer late).
  const latest = useRef(0);
  // A shorter list must not pull the page up either: after a pick, the rows' box keeps
  // at least the height that was on screen below its top.
  const box = useRef<HTMLDivElement>(null);
  const [minHeight, setMinHeight] = useState(0);

  const load = useCallback(() => {
    const mine = ++latest.current;
    api.jobs
      .list(LIMIT, filter.filter)
      .then((j) => {
        if (mine !== latest.current) return;
        setJobs(j);
        setError(null);
      })
      .catch((e: Error) => mine === latest.current && setError(e.message))
      .finally(() => mine === latest.current && setSwitching(false));
    api.jobs
      .list(LIMIT, { status: "failed" })
      .then((f) => setFailedToday(f.filter((j) => Date.now() - new Date(j.created_at).getTime() < DAY_MS).length))
      .catch(() => setFailedToday(0));
  }, [filter]);

  useEffect(() => {
    setSwitching(true);
    load();
  }, [load, reloadKey]);
  const anyActive = (jobs ?? []).some((j) => isActive(j.status));
  useActivePolling(load, ACTIVE_POLL_MS, anyActive);
  useActivePolling(load, IDLE_POLL_MS, !anyActive);

  function pick(key: RunFilter) {
    if (box.current) {
      const onScreen = window.innerHeight - box.current.getBoundingClientRect().top;
      setMinHeight(Math.max(0, Math.min(box.current.offsetHeight, onScreen)));
    }
    const next = new URLSearchParams(sp);
    if (key === "all") next.delete("runs");
    else next.set("runs", key);
    setSp(next, { replace: true, preventScrollReset: true });
  }

  async function remove(j: Job) {
    setConfirmDelete(null);
    try {
      if (j.kind === "snapshot") await api.iam.deleteScan(j.id);
      else await api.activity.deletePull(j.id);
      showToast(`${KIND_LABEL[j.kind]} #${j.id} deleted`, "success");
      load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  return (
    <SectionCard
      id="runs"
      icon="history"
      tone={failedToday > 0 ? "warn" : "info"}
      title="Recent checks and collections"
      hint={`The newest ${LIMIT}, as filtered.`}
      actions={
        <div className={FILTER_BAR} role="group" aria-label="Show">
          {RUN_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={pillClass(f.key === filter.key)}
              aria-pressed={f.key === filter.key}
              aria-label={f.label}
              onClick={() => pick(f.key)}
            >
              {f.label}
              {f.key === "failed" && failedToday > 0 && (
                <span className="text-tertiary" title="failed in the last 24 hours">
                  {" "}
                  {failedToday}
                </span>
              )}
            </button>
          ))}
        </div>
      }
    >
      {error && <p className="my-[1em] text-error-primary">Could not load the runs: {error}</p>}
      <div ref={box} style={minHeight ? { minHeight } : undefined}>
        {jobs !== null && jobs.length === 0 ? (
          <p className={cx("my-[1em]", EMPTY_STATE)}>{filter.key === "failed" ? "Nothing has failed." : "Nothing has run yet."}</p>
        ) : (
          <RunsTable jobs={jobs} names={names} switching={switching} confirmDelete={confirmDelete}
            onConfirm={setConfirmDelete} onDelete={(j) => void remove(j)} />
        )}
      </div>
    </SectionCard>
  );
}

type TableProps = {
  jobs: Job[] | null;
  names: ReadonlyMap<string, string>;
  switching: boolean;
  /** The row whose Delete was pressed once, as ``kind-id``. */
  confirmDelete: string | null;
  onConfirm: (key: string | null) => void;
  onDelete: (j: Job) => void;
};

function RunsTable({ jobs, names, switching, confirmDelete, onConfirm, onDelete }: TableProps) {
  return (
    <div className={cx(TABLE_SCROLL, switching && jobs !== null && "opacity-55 [transition:opacity_120ms_ease-out]")} aria-busy={switching}>
      <table className={TABLE_COMPACT}>
        <thead>
          <tr>
            <th scope="col">State</th>
            <th scope="col">What</th>
            <th scope="col">Where</th>
            <th scope="col">Started</th>
            <th scope="col">Took</th>
            <th scope="col">Result</th>
            <th scope="col" aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {jobs === null && (
            <tr>
              <td colSpan={7} className="text-quaternary">
                Loading…
              </td>
            </tr>
          )}
          {(jobs ?? []).map((j) => {
            const t = targetDisplay(j.kind, j.target, names);
            const key = `${j.kind}-${j.id}`;
            return (
              <tr key={key}>
                <td>
                  <StatePill tone={STATUS_TONE[j.status]}>{j.status}</StatePill>
                </td>
                <td>
                  <Link to={jobHref(j)}>
                    {KIND_LABEL[j.kind]} #{j.id}
                  </Link>
                  {j.origin && <div className={CAPTION_MUTED}>{ORIGIN_LABEL[j.origin]}</div>}
                </td>
                <td className={CELL_TARGET} title={j.target}>
                  {t.primary}
                  {j.window_start && j.window_end && (
                    <div className={CAPTION_MUTED}>
                      {formatDateTime(j.window_start)} → {formatDateTime(j.window_end)}
                    </div>
                  )}
                </td>
                <td>{formatDateTime(j.started_at ?? j.created_at)}</td>
                <td>{durationText(j.started_at, j.finished_at)}</td>
                <td>
                  <JobResult job={j} />
                </td>
                <td>
                  {!isActive(j.status) &&
                    (confirmDelete === key ? (
                      <div className={ROW_ACTIONS} role="group" aria-label="Confirm delete">
                        <Button variant="primary" size="sm" type="button" onClick={() => onDelete(j)}>
                          Delete
                        </Button>
                        <Button size="sm" type="button" onClick={() => onConfirm(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <Button
                        size="sm"
                        type="button"
                        title={
                          j.kind === "snapshot"
                            ? "Delete this check and what it found. Refused while it is a place's current data."
                            : "Delete this record. The events it collected stay."
                        }
                        onClick={() => onConfirm(key)}
                      >
                        Delete
                      </Button>
                    ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function JobResult({ job }: { job: Job }) {
  if (job.status === "failed") return <ErrorText summary={job.error_summary} raw={job.error ?? "failed"} />;
  if (isActive(job.status)) {
    return (
      <div className="flex min-w-50 flex-col gap-1">
        <div className="h-2 w-full overflow-hidden rounded-[2px] bg-primary_hover">
          <div className="h-full w-[35%] animate-[progress-slide_1.4s_ease-in-out_infinite] rounded-[2px] bg-(--accent-blue) [transition:width_200ms_ease-out]" />
        </div>
        <span className={CAPTION_MUTED}>
          {job.status === "pending" ? "queued" : (job.progress_message ?? "starting…")}
        </span>
      </div>
    );
  }
  if (job.kind === "activity_pull") {
    const n = (job.events_persisted ?? 0).toLocaleString();
    // Management groups Azure refused (not allowed, or deleted since the last check).
    const refused = /· Not readable: (.+)$/.exec(job.progress_message ?? "")?.[1];
    return (
      <span className={CAPTION}>
        {job.origin === "recheck" ? `${n} late events` : `${n} new changes`}
        {refused && ` · could not read ${refused}`}
      </span>
    );
  }
  return (
    <span className={CAPTION}>
      {job.progress_message ?? "done"}
      {(job.unread ?? 0) > 0 &&
        ` · ${job.unread} ${job.unread === 1 ? "place" : "places"} could not be read (not allowed)`}
    </span>
  );
}
