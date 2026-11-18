import { useEffect, useState } from "react";

import Button from "@/components/Button";
import { Link } from "react-router-dom";

import { api, type DatabaseStats } from "@/api/client";
import { CAPTION, CARD, FORM_ROW, KV_LIST, MONO, cx } from "@/components/classes";
import { useConfirm } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { SectionCard } from "@/components/PageTemplate";

import { TABLE } from "./AuditTab";

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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the tab opens
  }, []);

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

      <p className={cx(CAPTION, "my-2")}>
        How long each kind of data is kept, and deleting old checks:{" "}
        <Link to="/settings/data-kept">Data kept</Link>.
      </p>
    </>
  );
}
