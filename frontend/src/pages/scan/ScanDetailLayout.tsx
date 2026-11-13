import { useEffect, useState } from "react";
import { Outlet, useOutletContext, useParams } from "react-router-dom";

import { api, type Scan } from "@/api/client";
import { useActivePolling } from "@/hooks/useActivePolling";

import ScanSummaryHeader from "./ScanSummaryHeader";

export type ScanContext = { scan: Scan | null };

/**
 * Shared chrome for a single snapshot: the summary header (with live
 * progress while pending/running) above SnapshotRedirect, which sends a
 * completed snapshot to its scope page. Loads the scan once and exposes
 * it to the child via Outlet context.
 */
export default function ScanDetailLayout() {
  const { scanId } = useParams();
  const id = Number(scanId);
  const [scan, setScan] = useState<Scan | null>(null);

  async function refreshScan() {
    if (!Number.isFinite(id)) return;
    try {
      setScan(await api.iam.getScan(id));
    } catch {
      // Surface in summary header (it gates on scan !== null). Children
      // that depend on scan status will idle until it loads.
    }
  }

  useEffect(() => {
    setScan(null);
    refreshScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const isActive = scan?.status === "pending" || scan?.status === "running";
  useActivePolling(refreshScan, 2000, isActive);

  return (
    <>
      <ScanSummaryHeader scan={scan} />
      <Outlet context={{ scan } satisfies ScanContext} />
    </>
  );
}

export function useScanContext(): ScanContext {
  return useOutletContext<ScanContext>();
}
