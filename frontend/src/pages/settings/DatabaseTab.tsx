import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { api, type DatabaseStats, type RetentionPolicy } from "@/api/client";
import { CAPTION, CARD, FORM_ROW, KV_LIST, MONO, cx } from "@/components/classes";
import { useConfirm } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";

import { TABLE } from "./AuditTab";
import { SETTINGS_FIELD, SETTINGS_FORM, SETTINGS_LABEL } from "./CredentialsTab";

function formatBytes(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = value / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}

function formatNumber(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

export default function DatabaseTab() {
  const { showToast } = useToast();
  const confirm = useConfirm();
  const [stats, setStats] = useState<DatabaseStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(false);
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cleanupDays, setCleanupDays] = useState(30);
  const [cleanupOnlyFailed, setCleanupOnlyFailed] = useState(false);
  const [policy, setPolicy] = useState<RetentionPolicy | null>(null);

  async function refresh() {
    setLoadingStats(true);
    setError(null);
    try {
      setStats(await api.database.getStats());
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      showToast(`Failed to load stats: ${msg}`, "error");
    } finally {
      setLoadingStats(false);
    }
  }

  useEffect(() => {
    refresh();
    api.database.retentionPolicy().then(setPolicy).catch(() => setPolicy(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the tab opens
  }, []);

  async function applyRetention() {
    const ok = await confirm({
      title: "Apply the retention policy now?",
      body: "Deleted events and checks cannot be restored.",
      action: "Apply now",
      danger: true,
    });
    if (!ok) return;
    setRunning("retention");
    try {
      const r = await api.database.applyRetention();
      showToast(
        `Deleted ${r.events_deleted.toLocaleString()} events, ${r.snapshots_deleted + r.failed_snapshots_deleted} checks, ${r.audit_deleted} audit rows in ${r.duration_ms} ms.`,
        "success",
      );
      await refresh();
    } catch (e) {
      showToast(`Retention failed: ${(e as Error).message}`, "error");
    } finally {
      setRunning(null);
    }
  }

  async function vacuumFull() {
    const ok = await confirm({
      title: "Run VACUUM FULL?",
      body: "It rewrites every table and locks each one while it runs (minutes for activity_events).",
      action: "Run VACUUM FULL",
    });
    if (!ok) return;
    setRunning("vacuum-full");
    try {
      const result = await api.database.vacuum(true);
      showToast(`VACUUM FULL completed in ${Math.round(result.duration_ms / 1000)} s.`, "success");
      await refresh();
    } catch (e) {
      showToast(`VACUUM FULL failed: ${(e as Error).message}`, "error");
    } finally {
      setRunning(null);
    }
  }

  async function runMaintenance(
    op: "vacuum" | "analyze" | "reindex",
    label: string,
  ) {
    setRunning(op);
    setError(null);
    try {
      const result = await api.database[op]();
      showToast(
        `${label} completed in ${result.duration_ms} ms.`,
        "success",
      );
      await refresh();
    } catch (e) {
      showToast(`${label} failed: ${(e as Error).message}`, "error");
    } finally {
      setRunning(null);
    }
  }

  async function runCleanup(e: React.FormEvent) {
    e.preventDefault();
    if (cleanupDays < 1) return;
    const ok = await confirm({
      title: cleanupOnlyFailed
        ? `Delete failed checks older than ${cleanupDays} day(s)?`
        : `Delete ALL checks older than ${cleanupDays} day(s)?`,
      body: "Deleted checks cannot be restored.",
      action: "Delete",
      danger: true,
    });
    if (!ok) return;
    setRunning("cleanup");
    setError(null);
    try {
      const result = await api.database.cleanupScans({
        older_than_days: cleanupDays,
        only_failed: cleanupOnlyFailed,
      });
      showToast(
        `Deleted ${result.rows_affected ?? 0} check(s) in ${result.duration_ms} ms.`,
        "success",
      );
      await refresh();
    } catch (e) {
      showToast(`Cleanup failed: ${(e as Error).message}`, "error");
    } finally {
      setRunning(null);
    }
  }

  if (!stats) {
    return (
      <div className={CARD}>
        {error ? <p className="my-[1em] text-error-primary">Error: {error}</p> : <p className="my-[1em]">Loading…</p>}
      </div>
    );
  }

  return (
    <>
      <SectionCard
        icon="data"
        title="Database"
      >
        <ul className={KV_LIST}>
          <li>
            <span className={CAPTION}>Dialect</span>
            <strong>{stats.dialect}</strong>
          </li>
          <li>
            <span className={CAPTION}>Total size</span>
            <strong>{formatBytes(stats.database_size_bytes)}</strong>
          </li>
          <li>
            <span className={CAPTION}>Tables</span>
            <strong>{stats.tables.length}</strong>
          </li>
          <li>
            <span className={CAPTION}>Indexes</span>
            <strong>{stats.indexes.length}</strong>
          </li>
          <li>
            <span className={CAPTION}>Refreshed</span>
            <strong>{new Date(stats.fetched_at).toLocaleString()}</strong>
          </li>
        </ul>
        <div className={FORM_ROW}>
          <Button
            size="sm"
            type="button"
            onClick={refresh}
            disabled={loadingStats}
          >
            {loadingStats ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </SectionCard>

      <SectionCard
        icon="data"
        title="Tables"
      >
        <table className={TABLE}>
          <thead>
            <tr>
              <th>Table</th>
              <th>Rows</th>
              <th>Total size</th>
              <th>Table size</th>
              <th>Index size</th>
            </tr>
          </thead>
          <tbody>
            {stats.tables.map((t) => (
              <tr key={t.name}>
                <td className={MONO}>{t.name}</td>
                <td>{formatNumber(t.row_count)}</td>
                <td>{formatBytes(t.total_size_bytes)}</td>
                <td>{formatBytes(t.table_size_bytes)}</td>
                <td>{formatBytes(t.index_size_bytes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </SectionCard>

      {stats.indexes.length > 0 && (
        <SectionCard
          icon="data"
          title="Indexes"
        >
          <table className={TABLE}>
            <thead>
              <tr>
                <th>Index</th>
                <th>Table</th>
                <th>Size</th>
                <th>Unique</th>
              </tr>
            </thead>
            <tbody>
              {stats.indexes.map((i) => (
                <tr key={i.name}>
                  <td className={MONO}>{i.name}</td>
                  <td className={MONO}>{i.table_name}</td>
                  <td>{formatBytes(i.size_bytes)}</td>
                  <td>{i.is_unique ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </SectionCard>
      )}

      <SectionCard
        icon="settings"
        title="Maintenance"
      >
        <p className="my-[1em] text-quaternary">
          Operational actions. <code>VACUUM</code> reclaims dead-row space and
          updates planner stats; <code>ANALYZE</code> only updates stats (cheap);{" "}
          <code>REINDEX</code> rebuilds every index in the database.{" "}
          <code>VACUUM FULL</code> rewrites tables to give freed space back to the disk, locking each
          table while it runs.
        </p>
        <div className={FORM_ROW}>
          <Button
            type="button"
            onClick={() => runMaintenance("vacuum", "VACUUM")}
            disabled={running !== null}
          >
            {running === "vacuum" ? "Running…" : "VACUUM"}
          </Button>
          <Button
            type="button"
            onClick={() => runMaintenance("analyze", "ANALYZE")}
            disabled={running !== null}
          >
            {running === "analyze" ? "Running…" : "ANALYZE"}
          </Button>
          <Button
            type="button"
            onClick={() => runMaintenance("reindex", "REINDEX")}
            disabled={running !== null}
          >
            {running === "reindex" ? "Running…" : "REINDEX"}
          </Button>
          <Button type="button" onClick={vacuumFull} disabled={running !== null}>
            {running === "vacuum-full" ? "Running…" : "VACUUM FULL"}
          </Button>
        </div>
      </SectionCard>

      <SectionCard
        icon="calendar"
        title="Retention"
      >
        {policy ? (
          <>
            <ul className="my-[1em] list-disc pl-10 text-quaternary">
              <li>
                Activity Log events:{" "}
                {policy.event_retention_days ? `${policy.event_retention_days} days` : "kept forever"}
              </li>
              <li>
                Checks:{" "}
                {policy.snapshot_keep_per_scope
                  ? `newest ${policy.snapshot_keep_per_scope} per place, plus any check a place still shows`
                  : "all kept"}
                {policy.failed_snapshot_retention_days
                  ? `; failed ones after ${policy.failed_snapshot_retention_days} days`
                  : ""}
              </li>
              <li>
                Audit log: {policy.audit_retention_days ? `${policy.audit_retention_days} days` : "kept forever"}
              </li>
              <li>
                {policy.interval_hours
                  ? `Applied automatically every ${policy.interval_hours} h.`
                  : "Automatic runs are disabled (RETENTION_INTERVAL_HOURS=0)."}
              </li>
            </ul>
            <p className={cx("my-[1em]", CAPTION, "text-quaternary")}>
              Set with environment variables (<code>EVENT_RETENTION_DAYS</code>,{" "}
              <code>SNAPSHOT_KEEP_PER_SCOPE</code>, <code>FAILED_SNAPSHOT_RETENTION_DAYS</code>,{" "}
              <code>AUDIT_RETENTION_DAYS</code>); 0 keeps everything. Freed space is reused by new rows;
              VACUUM FULL returns it to the disk.
            </p>
          </>
        ) : (
          <p className="my-[1em] text-quaternary">Loading policy…</p>
        )}
        <div className={FORM_ROW}>
          <Button variant="primary" type="button" onClick={applyRetention} disabled={running !== null}>
            {running === "retention" ? "Applying…" : "Apply now"}
          </Button>
        </div>
      </SectionCard>

      <SectionCard
        icon="history"
        title="Delete old checks"
      >
        <p className="my-[1em] text-quaternary">
          Deletes finished checks older than this, with the grants they found. Checks still
          running, and the check each place shows now, are kept.
        </p>
        <form onSubmit={runCleanup} className={SETTINGS_FORM}>
          <label className={SETTINGS_FIELD}>
            <span className={SETTINGS_LABEL}>Older than (days)</span>
            <input
              className="font-normal tracking-normal"
              type="number"
              min={1}
              value={cleanupDays}
              onChange={(e) => setCleanupDays(Number(e.target.value) || 0)}
            />
          </label>
          <label className={SETTINGS_FIELD}>
            <span className={SETTINGS_LABEL}>
              <input
                className="text-[13.3333px] leading-[normal] font-normal tracking-normal [color:fieldtext] mt-[3px] mr-[3px] mb-[3px] ml-1"
                type="checkbox"
                checked={cleanupOnlyFailed}
                onChange={(e) => setCleanupOnlyFailed(e.target.checked)}
              />{" "}
              Only delete failed checks
            </span>
          </label>
          <div className={FORM_ROW}>
            <Button
              variant="primary"
              type="submit"
              disabled={running !== null || cleanupDays < 1}
            >
              {running === "cleanup" ? "Deleting…" : "Run cleanup"}
            </Button>
          </div>
        </form>
      </SectionCard>
    </>
  );
}
