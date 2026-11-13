import { useState } from "react";
import { useNavigate } from "react-router-dom";

import Button from "@/components/Button";
import { api, type Scan } from "@/api/client";
import { BADGE, BADGE_LEVEL, CAPTION, CARD, MONO } from "@/components/classes";
import { PageHeader, StatePill, type Tone } from "@/components/PageTemplate";
import { useToast } from "@/components/Toast";
import { formatDateTime, LEVEL_SHORT, scopeLabel } from "@/pages/scopes/scopeUtils";
import { cx } from "@/components/classes";

const RUNS = "/admin/data?runs=checks#runs";

/** The bar of a running check; without a total it slides back and forth. */
const PROGRESS_BAR = "h-full rounded-[2px] bg-(--accent-blue) [transition:width_200ms_ease-out]";
const PROGRESS_SLIDE = "w-[35%] animate-[progress-slide_1.4s_ease-in-out_infinite]";

const STATE: Record<Scan["status"], { tone: Tone; text: string }> = {
  pending: { tone: "info", text: "waiting" },
  running: { tone: "info", text: "running" },
  done: { tone: "ok", text: "done" },
  failed: { tone: "danger", text: "failed" },
};

function Progress({ scan }: { scan: Scan }) {
  if (scan.status !== "pending" && scan.status !== "running") return null;
  const { progress_done, progress_total, progress_message } = scan;
  const pct =
    progress_total && progress_total > 0 ? Math.min(100, Math.round((progress_done / progress_total) * 100)) : null;
  return (
    <div className="mt-2">
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className={CAPTION}>{progress_message ?? "Working…"}</span>
        <span className={cx(MONO, "text-quaternary")}>
          {progress_total != null
            ? `${progress_done} / ${progress_total}${pct != null ? ` (${pct}%)` : ""}`
            : `${progress_done} found`}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-[2px] bg-primary_hover">
        <div className={cx(PROGRESS_BAR, pct == null && PROGRESS_SLIDE)} style={{ width: pct != null ? `${pct}%` : undefined }} />
      </div>
    </div>
  );
}

/**
 * The head of one check's page: which place, its state, live progress while
 * it runs, and the reason when it failed. A finished check opens its place
 * (SnapshotRedirect), so this is seen while a check runs or after it failed.
 */
export default function ScanSummaryHeader({ scan }: { scan: Scan | null }) {
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [starting, setStarting] = useState(false);

  if (!scan) {
    return (
      <PageHeader back={{ to: RUNS, label: "Recent checks" }} title="Check" />
    );
  }

  async function checkAgain() {
    if (!scan) return;
    setStarting(true);
    try {
      const fresh = await api.iam.startScan(scan.scope);
      showToast(`Check #${fresh.id} started`, "success");
      navigate(`/iam-access/${fresh.id}`);
    } catch (e) {
      showToast(`Could not start the check: ${(e as Error).message}`, "error");
    } finally {
      setStarting(false);
    }
  }

  const state = STATE[scan.status];
  const when = scan.finished_at ?? scan.started_at;
  return (
    <>
      <PageHeader
        back={{ to: RUNS, label: "Recent checks" }}
        title={`Check #${scan.id}`}
        badges={<StatePill tone={state.tone}>{state.text}</StatePill>}
        sub={
          <>
            <span className={cx(BADGE, BADGE_LEVEL[scan.scope_level])}>{LEVEL_SHORT[scan.scope_level]}</span>{" "}
            {scopeLabel(scan.scope, scan.scope_name)}
            {when && ` · ${scan.finished_at ? "finished" : "started"} ${formatDateTime(when)}`}
            {scan.role_assignment_count > 0 && ` · ${scan.role_assignment_count} grants found`}
          </>
        }
        actions={
          scan.status === "failed" && (
            <Button size="sm" type="button" onClick={checkAgain} disabled={starting}>
              {starting ? "Starting…" : "Check again"}
            </Button>
          )
        }
      />
      <Progress scan={scan} />
      {scan.error && (
        <div className={CARD}>
          <p className="text-error-primary" style={{ margin: 0 }}>
            {scan.error}
          </p>
        </div>
      )}
    </>
  );
}
