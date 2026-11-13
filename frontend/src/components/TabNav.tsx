import type { ReactNode } from "react";

import { Badge } from "@/ui/untitled/components/base/badges/badges";

/**
 * Section tabs in Untitled UI's underline look (docs/plan-untitled-ui.md, phase 3).
 * They stay links in a <nav> (each section has its own address), not ARIA tabs.
 * The bottom line is an inset shadow, so the row can scroll sideways on a phone
 * without clipping the chosen tab's underline.
 */
export function TabNav({ label, children }: { label: string; children: ReactNode }) {
  return (
    <nav
      aria-label={label}
      className="tab-nav uui mb-4 flex gap-4 overflow-x-auto shadow-[inset_0_-1px_0_var(--border-card)] scrollbar-hide print:hidden"
    >
      {children}
    </nav>
  );
}

/** One tab (a link). The chosen one is underlined in the brand colour. */
export function tabClass(active: boolean): string {
  const base =
    "flex flex-none items-center gap-2 whitespace-nowrap border-b-2 px-0.5 pb-2.5 text-sm font-semibold no-underline outline-focus-ring transition duration-100 ease-linear focus-visible:outline-2 focus-visible:-outline-offset-2";
  return active
    ? `${base} active border-fg-brand-primary_alt text-brand-secondary`
    : `${base} border-transparent text-quaternary hover:border-fg-brand-primary_alt hover:text-brand-secondary`;
}

/** A count beside a tab's name; red when it counts problems. */
export function TabCount({ n, problem = false }: { n: number; problem?: boolean }) {
  return (
    <Badge type="pill-color" size="sm" color={problem ? "error" : "gray"}>
      {n.toLocaleString()}
    </Badge>
  );
}
