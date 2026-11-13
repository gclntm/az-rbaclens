import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { ChangesResponse } from "@/api/client";
import { ChartLegendContent, ChartTooltipContent } from "@/ui/untitled/components/application/charts/charts-base";

type Week = ChangesResponse["weeks"][number];

/** "3 Nov": the day a week starts. */
export function weekLabel(weekStart: string): string {
  return new Date(weekStart).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** One week in words, for screen readers and the tooltip's heading. */
export function weekText(w: Week): string {
  return `Week of ${weekLabel(w.week_start)}: ${w.given} given, ${w.removed} removed`;
}

/**
 * Access given and removed per week, as stacked bars (Untitled UI's bar chart on
 * recharts, docs/plan-untitled-ui.md phase 5). Green is given and red removed,
 * our accents in both themes; no animation. Loaded on its own (React.lazy in
 * ChangesFeed), so pages without the chart don't download recharts.
 */
export default function WeekChart({ weeks }: { weeks: Week[] }) {
  return (
    <figure className="uui m-0" aria-label="Changes per week">
      <div className="h-56" aria-hidden="true">
        <ResponsiveContainer initialDimension={{ width: 1, height: 1 }}>
          <BarChart
            data={weeks}
            className="text-tertiary [&_.recharts-text]:text-xs"
            margin={{ left: 0, right: 0, top: 8, bottom: 0 }}
          >
            <CartesianGrid vertical={false} stroke="currentColor" className="text-utility-neutral-100" />
            <Legend verticalAlign="top" align="right" content={<ChartLegendContent className="pb-2" />} />
            <XAxis
              dataKey="week_start"
              fill="currentColor"
              axisLine={false}
              tickLine={false}
              tickMargin={8}
              interval="preserveStartEnd"
              tickFormatter={(v: string) => weekLabel(v)}
            />
            <YAxis fill="currentColor" axisLine={false} tickLine={false} allowDecimals={false} width={32} />
            <Tooltip
              content={<ChartTooltipContent />}
              labelFormatter={(v) => `Week of ${weekLabel(String(v))}`}
              cursor={{ className: "fill-utility-neutral-200/20" }}
            />
            <Bar
              dataKey="given"
              name="Given"
              stackId="week"
              fill="currentColor"
              className="text-(--accent-green)"
              isAnimationActive={false}
              maxBarSize={32}
            />
            <Bar
              dataKey="removed"
              name="Removed"
              stackId="week"
              fill="currentColor"
              className="text-(--accent-red)"
              isAnimationActive={false}
              maxBarSize={32}
              radius={[4, 4, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ul className="sr-only">
        {weeks.map((w) => (
          <li key={w.week_start}>{weekText(w)}</li>
        ))}
      </ul>
    </figure>
  );
}
