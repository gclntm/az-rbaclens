import { useId } from "react";

import { NAV_ICONS, type NavIconName } from "@/components/NavIcon";

export type LoopNode = { icon: NavIconName; title: string; short: string };

// The ring: an ellipse, the steps clockwise from the top. Theme colours come
// from CSS variables, so the drawing follows light and dark.
const W = 760;
const H = 350;
const CX = 380;
const CY = 196;
const RX = 200;
const RY = 116;
const NODE = 28;
const GAP_DEG = 15; // where an arrow starts and stops, either side of a step
const BRAND = "var(--color-fg-brand-primary)";
const BRAND_BG = "var(--color-bg-brand-secondary)";
const TEXT = "var(--color-text-primary)";
const MUTED = "var(--color-text-tertiary)";
const LINE = "var(--color-fg-quaternary)";

function at(deg: number): [number, number] {
  const r = (deg * Math.PI) / 180;
  return [CX + RX * Math.cos(r), CY + RY * Math.sin(r)];
}

/**
 * The workflow as a loop: each step on a ring, arrows clockwise, the idea in
 * the middle. Decorative support for the cards below it, which carry the same
 * words and the links; hidden on narrow screens, where it would be unreadable.
 */
export default function HelpLoopDiagram({ nodes }: { nodes: LoopNode[] }) {
  const arrow = useId().replace(/:/g, "");
  const step = 360 / nodes.length;
  const angles = nodes.map((_, i) => -90 + i * step);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="mx-auto mb-6 block h-auto w-full max-w-[760px] max-[600px]:hidden"
      role="img"
      aria-labelledby={`${arrow}-t ${arrow}-d`}
    >
      <title id={`${arrow}-t`}>The RBAC Lens loop</title>
      <desc id={`${arrow}-d`}>{nodes.map((n) => n.title).join(", then ")}, and round again.</desc>
      <defs>
        <marker
          id={arrow}
          viewBox="0 0 10 10"
          refX="8"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill={LINE} />
        </marker>
      </defs>

      {angles.map((a, i) => {
        const [x1, y1] = at(a + GAP_DEG);
        const [x2, y2] = at(a + step - GAP_DEG);
        return (
          <path
            key={`arc-${i}`}
            d={`M${x1},${y1} A${RX},${RY} 0 0 1 ${x2},${y2}`}
            fill="none"
            stroke={LINE}
            strokeWidth="2"
            strokeLinecap="round"
            markerEnd={`url(#${arrow})`}
          />
        );
      })}

      <text textAnchor="middle" fontSize="15" fill={MUTED}>
        <tspan x={CX} y={CY - 22}>RBAC Lens finds,</tspan>
        <tspan x={CX} dy="21">you decide,</tspan>
        <tspan x={CX} dy="21">someone acts in Azure,</tspan>
        <tspan x={CX} dy="21">the next check confirms.</tspan>
      </text>

      {nodes.map((n, i) => {
        const [x, y] = at(angles[i]);
        const cos = Math.cos((angles[i] * Math.PI) / 180);
        // Beside the step on the sides, above it at the top (clear of the arrows).
        const anchor = cos > 0.2 ? "start" : cos < -0.2 ? "end" : "middle";
        const lx = anchor === "start" ? x + NODE + 12 : anchor === "end" ? x - NODE - 12 : x;
        const ly = anchor === "middle" ? y - NODE - 30 : y - 2;
        const Icon = NAV_ICONS[n.icon];
        return (
          <g key={n.title}>
            <circle cx={x} cy={y} r={NODE} fill={BRAND_BG} />
            <g transform={`translate(${x - 12},${y - 12})`} style={{ color: BRAND }} aria-hidden="true">
              <Icon size={24} strokeWidth={1.75} />
            </g>
            <text x={lx} y={ly} textAnchor={anchor} fontSize="16" fontWeight="600" fill={TEXT}>
              {i + 1}. {n.title}
            </text>
            <text x={lx} y={ly + 19} textAnchor={anchor} fontSize="13" fill={MUTED}>
              {n.short}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
