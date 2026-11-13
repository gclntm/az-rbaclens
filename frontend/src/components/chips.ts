/**
 * Small controls in Untitled UI's look (docs/plan-untitled-ui.md, phase 3), as
 * class names so each keeps its element: a filter pill may be a button, a label
 * around a checkbox, or a link. Each is a .uui region (styles/uui-reset.css), so
 * old element rules don't reach in; the old class names stay as layout hooks.
 */

import { buttonClass } from "@/components/Button";
import { cx } from "@/components/classes";

const FOCUS = "outline-focus-ring focus-visible:outline-2 focus-visible:outline-offset-2";

/** A filter or toggle pill. The chosen one is our chip blue, as in the side menu. */
export function pillClass(active: boolean): string {
  const base = `pill uui inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-full px-3 text-sm font-medium no-underline shadow-xs ring-1 ring-inset transition duration-100 ease-linear ${FOCUS}`;
  return active
    ? `${base} is-active bg-[var(--chip-active-bg)] text-primary ring-[var(--chip-active-border)]`
    : `${base} bg-primary text-secondary ring-primary hover:bg-primary_hover hover:text-secondary_hover`;
}

/** A button that reads as a link, in running text ("Clear all filters"). ``small`` for caption-sized text. */
export function linkButtonClass(small = false): string {
  return `link-btn uui cursor-pointer rounded-sm font-medium text-brand-secondary hover:text-brand-secondary_hover hover:underline ${small ? "text-xs" : ""} ${FOCUS}`;
}

/** The small "Copy" button beside an id. */
export function copyButtonClass(): string {
  return `copy-btn uui cursor-pointer rounded-md px-1.5 py-0.5 text-xs font-semibold text-tertiary hover:bg-primary_hover hover:text-secondary ${FOCUS}`;
}

/** A review decision once chosen: its own colour, which says what was decided. */
const DECIDED = {
  keep: "bg-(--accent-green) ring-(--accent-green) hover:bg-(--accent-green)",
  remove: "bg-(--accent-red) ring-(--accent-red) hover:bg-(--accent-red)",
  change: "bg-(--accent-blue) ring-(--accent-blue) hover:bg-(--accent-blue)",
  ask: "bg-(--accent-orange) ring-(--accent-orange) hover:bg-(--accent-orange)",
} as const;

/**
 * Keep / remove / change / ask on a grant or review item: a secondary button,
 * filled with the decision's colour once chosen; the advice's suggestion has a
 * dashed outline until someone decides.
 */
export function decisionClass(d: keyof typeof DECIDED, on: boolean, suggested = false): string {
  return cx(
    buttonClass("secondary", "sm"),
    `review-${d} min-w-24`,
    on && `is-on ${DECIDED[d]} text-(--on-accent) hover:text-(--on-accent)`,
    suggested && "is-suggested outline-2 outline-offset-2 outline-(--accent-blue) outline-dashed",
  );
}
