import { Navigate, Route, Routes } from "react-router-dom";

import IamAccessDiffPage from "@/pages/IamAccessDiffPage";
import ScanDetailLayout from "@/pages/scan/ScanDetailLayout";
import SnapshotRedirect from "@/pages/scan/SnapshotRedirect";

/**
 * Old addresses of checks. The list of checks lives on Data & schedules now
 * (recent runs, filtered to checks); a single check opens the place it checked.
 */
export default function IamAccessPage() {
  return (
    <Routes>
      <Route index element={<Navigate replace to="/admin/data?runs=checks#runs" />} />
      <Route path="diff/:oldId/:newId" element={<IamAccessDiffPage />} />
      <Route path=":scanId" element={<ScanDetailLayout />}>
        {/* Per-snapshot views retired: Scan view + Inspector (phase 2) open the
            scope page; Coverage (phase 3) opens its Findings tab. */}
        <Route index element={<SnapshotRedirect />} />
        <Route path="scan-view" element={<SnapshotRedirect />} />
        <Route path="inspector" element={<SnapshotRedirect />} />
        <Route path="coverage" element={<SnapshotRedirect tab="findings" />} />
      </Route>
    </Routes>
  );
}
