import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import Button from "@/components/Button";
import {
  api,
  type Coverage,
  type Freshness,
  type JobSchedule,
  type JobScheduleUpdate,
  type RetentionPolicy,
  type ScopeNode,
  type SubscriptionNode,
} from "@/api/client";
import { CAPTION, cx } from "@/components/classes";
import { whenText } from "@/components/FreshnessLine";
import { PageHeader, SectionCard, StatusTile, StatusTiles } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { useActivePolling } from "@/hooks/useActivePolling";

import { checkTile, collectionTile, schedulesTile } from "./coverageUtils";
import CoveragePanel from "./CoveragePanel";
import RecentRuns from "./RecentRuns";
import ScheduleForm from "./ScheduleForm";
import SchedulesTable from "./SchedulesTable";
import {
  isActive,
  KIND_LABEL,
  orderSchedules,
  placeNames,
  scheduleCounts,
  targetDisplay,
} from "./jobUtils";

const ACTIVE_POLL_MS = 2000;
// Scheduled jobs start on their own, so keep an idle page roughly current.
const IDLE_POLL_MS = 30_000;

/** A warning across the page: what needs doing, and the way out. */
const BANNER =
  "flex flex-wrap items-center justify-between gap-4 rounded-[8px] border px-6 py-4 text-[0.875rem]";
const BANNER_TONE = {
  warn: "border-(--accent-orange) bg-(--accent-orange-subtle)",
  danger: "border-(--accent-red) bg-(--accent-red-subtle)",
} as const;

/**
 * Data & schedules: how RBAC Lens keeps its copy of Azure current. Built on the page
 * template (components/PageTemplate): three status tiles answer "is everything fine?",
 * a banner appears only when something needs doing, then the sections: Activity Log
 * coverage, schedules, recent runs. Route: /admin/data (``?runs=`` filters the runs).
 */
export default function JobsPage() {
  const { showToast } = useToast();
  const [fresh, setFresh] = useState<Freshness | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [retention, setRetention] = useState<RetentionPolicy | null>(null);
  const [schedules, setSchedules] = useState<JobSchedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubscriptionNode[]>([]);
  const [subsLoading, setSubsLoading] = useState(false);
  const [subsError, setSubsError] = useState<string | null>(null);
  const [tree, setTree] = useState<ScopeNode[]>([]);
  const [adding, setAdding] = useState(false);
  // Bumped when this page starts or changes something the runs list should show.
  const [runsKey, setRunsKey] = useState(0);

  const names = useMemo(() => placeNames(subscriptions, tree), [subscriptions, tree]);

  async function refresh() {
    try {
      const [s, f, c] = await Promise.all([api.jobs.schedules(), api.freshness(), api.jobs.coverage()]);
      setSchedules(s);
      setFresh(f);
      setCoverage(c);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function loadSubscriptions(fromAzure = false) {
    setSubsLoading(true);
    setSubsError(null);
    try {
      if (fromAzure) await api.scopeBrowser.refresh();
      setSubscriptions(await api.scopeBrowser.subscriptions());
      if (fromAzure) showToast("Subscription list refreshed", "success");
    } catch (e) {
      setSubsError((e as Error).message);
    } finally {
      setSubsLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
    void loadSubscriptions();
    // Admin-only; the card says "unknown" when it is not readable.
    api.database.retentionPolicy().then(setRetention).catch(() => setRetention(null));
    // Names of the places already checked, for the rows below.
    api.scopes.tree().then(setTree).catch(() => setTree([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the page opens
  }, []);

  const anyActive = (schedules ?? []).some((s) => isActive(s.last_job_status));
  useActivePolling(refresh, ACTIVE_POLL_MS, anyActive);
  useActivePolling(refresh, IDLE_POLL_MS, !anyActive);

  const nameOf = (s: JobSchedule) => `${KIND_LABEL[s.kind]} for ${targetDisplay(s.kind, s.target, names).primary}`;

  async function act(action: () => Promise<unknown>, success: string): Promise<boolean> {
    try {
      await action();
      showToast(success, "success");
      await refresh();
      setRunsKey((k) => k + 1);
      return true;
    } catch (e) {
      showToast((e as Error).message, "error");
      return false;
    }
  }

  /** Pause or resume; a pause can be undone from its toast. */
  async function setEnabled(s: JobSchedule, enabled: boolean) {
    try {
      await api.jobs.updateSchedule(s.id, { enabled });
      if (enabled) showToast(`Resumed: ${nameOf(s)}`, "success");
      else showToast(`Paused: ${nameOf(s)}`, "info", { label: "Undo", run: () => void setEnabled(s, true) });
      await refresh();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function resumeAll() {
    const off = (schedules ?? []).filter((s) => !s.enabled);
    try {
      await Promise.all(off.map((s) => api.jobs.updateSchedule(s.id, { enabled: true })));
      showToast(`Resumed ${off.length} ${off.length === 1 ? "schedule" : "schedules"}`, "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
    await refresh();
  }

  const ordered = useMemo(() => (schedules === null ? null : orderSchedules(schedules, names)), [schedules, names]);
  const counts = scheduleCounts(schedules ?? []);
  const daily = (schedules ?? []).find((s) => s.kind === "snapshot" && s.at_hour != null) ?? null;
  const timezone = fresh?.timezone || "UTC";
  const tiles = {
    check: checkTile(daily, schedules === null ? null : fresh, whenText),
    collection: collectionTile(coverage),
    schedules: schedules === null ? { tone: "info" as const, value: "…", note: "" } : schedulesTile(counts),
  };

  return (
    <>
      <PageHeader
        title="Data & schedules"
        sub="RBAC Lens keeps its copy of Azure up to date on its own. Nothing here is needed day to day."
        actions={
          <Button size="sm" to="/events">
            Activity Log
          </Button>
        }
      />
      {error && <p className="my-[1em] text-error-primary">{error}</p>}

      <StatusTiles>
        <StatusTile tone={tiles.check.tone} icon="shield" label="Who has which role" value={tiles.check.value} note={tiles.check.note} />
        <StatusTile tone={tiles.collection.tone} icon="changes" label="Who changed what" value={tiles.collection.value} note={tiles.collection.note} />
        <StatusTile tone={tiles.schedules.tone} icon="calendar" label="Schedules" value={tiles.schedules.value} note={tiles.schedules.note} />
      </StatusTiles>

      {/* Only when something needs doing: what, why it matters, and the way out. */}
      {(fresh?.warnings ?? []).map((w) => (
        <div key={w.kind} className={cx(BANNER, w.severity === "danger" ? BANNER_TONE.danger : BANNER_TONE.warn)} role="alert">
          <span>{w.text}</span>
          {counts.off > 0 && (w.kind === "schedules_off" || w.severity === "danger") && (
            <Button size="sm" type="button" onClick={() => void resumeAll()}>
              Resume all
            </Button>
          )}
        </div>
      ))}

      <CoveragePanel coverage={coverage} retentionDays={retention?.event_retention_days ?? null} />

      <SectionCard
        id="schedules"
        icon="calendar"
        tone={tiles.schedules.tone}
        title="Schedules"
        hint={
          <>
            What runs by itself, and when. The daily check reads who has which role in{" "}
            {daily ? targetDisplay(daily.kind, daily.target, names).primary : "the tenant"}; collection reads who
            changed what, per subscription.
            {retention &&
              ` Kept: changes ${retention.change_retention_days ?? 365} days, the newest ${retention.snapshot_keep_per_scope} checks per place.`}{" "}
            <Link to="/settings/database">Settings › Database</Link>
          </>
        }
        actions={
          <Button size="sm" type="button" aria-expanded={adding} onClick={() => setAdding(!adding)}>
            {adding ? "Close" : "Add a schedule, or run once"}
          </Button>
        }
      >
        {adding && (
          <div className="mb-4 border-b border-b-secondary pb-4">
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
              Check a place, or collect a subscription&apos;s Activity Log, on a schedule or once. Collection is
              incremental: each run fetches only what is new since the last one (Azure keeps about 90 days).
            </p>
            <ScheduleForm
              subscriptions={subscriptions}
              subsLoading={subsLoading}
              subsError={subsError}
              onRefreshSubscriptions={() => void loadSubscriptions(true)}
              onChanged={() => {
                void refresh();
                setRunsKey((k) => k + 1);
              }}
            />
          </div>
        )}
        <SchedulesTable
          schedules={ordered}
          names={names}
          timezone={timezone}
          onRun={(s) => void act(() => api.jobs.runSchedule(s.id), `Started: ${nameOf(s)}`)}
          onSetEnabled={(s, enabled) => void setEnabled(s, enabled)}
          onSave={(s, patch: JobScheduleUpdate) => act(() => api.jobs.updateSchedule(s.id, patch), `Saved: ${nameOf(s)}`)}
          onDelete={(s) => void act(() => api.jobs.deleteSchedule(s.id), `Deleted: ${nameOf(s)}`)}
        />
      </SectionCard>

      <RecentRuns names={names} reloadKey={runsKey} />
    </>
  );
}
