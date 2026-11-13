import { Link, Navigate } from "react-router-dom";

import { CARD } from "@/components/classes";
import { scopeHref, type ScopeTab } from "@/pages/scopes/scopeUtils";

import { useScanContext } from "./ScanDetailLayout";

/**
 * Old snapshot routes (/iam-access/:id, …/scan-view, …/inspector, …/coverage).
 *
 * The per-snapshot Scan view and Inspector were retired in favour of the
 * scope page, which shows the *current* state of a scope across
 * snapshots. A completed snapshot redirects there; one still in flight
 * stays here so the summary header above can show its progress.
 */
export default function SnapshotRedirect({ tab }: { tab?: ScopeTab }) {
  const { scan } = useScanContext();
  if (!scan) return null;
  if (scan.status === "done") return <Navigate to={scopeHref(scan.scope, tab)} replace />;
  return (
    <div className={CARD}>
      <p className="text-secondary" style={{ margin: 0 }}>
        {scan.status === "failed"
          ? "This check failed, so it has nothing to show. The place still shows its last good check. "
          : "This check is still running. What it finds appears on the place's page when it finishes. "}
        <Link to={scopeHref(scan.scope)}>Open the place →</Link>
      </p>
    </div>
  );
}
