import { lazy, Suspense } from "react";

import type { ChangesResponse } from "@/api/client";

const WeekChart = lazy(() => import("./WeekChart"));

/**
 * The changes-per-week chart, downloaded only when a page draws it (recharts is
 * large). Until then an empty block of the chart's height keeps the page still.
 */
export default function LazyWeekChart({ weeks }: { weeks: ChangesResponse["weeks"] }) {
  return (
    <Suspense fallback={<div className="h-56" aria-hidden="true" />}>
      <WeekChart weeks={weeks} />
    </Suspense>
  );
}
