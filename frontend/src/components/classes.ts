/**
 * The app's shared looks as Tailwind class strings on Untitled UI's tokens
 * (docs/design.md). They replace the old shared CSS classes
 * (.card, .table-compact, .badge…) one for one, with the same values: arbitrary
 * values where Tailwind's scale would change a size or a line height.
 *
 * Colour map (tokens.css): --text-primary = text-primary, --text-secondary =
 * text-tertiary, --text-muted = text-quaternary, --bg-card = bg-primary,
 * --bg-card-hover = bg-primary_hover, --bg-table-head = bg-secondary,
 * --border = border-secondary, --border-card = border-primary.
 *
 * Some old class names stay on the elements as inert hooks ("card" for print,
 * "status-tile"…); they carry no style of their own.
 */

import { extendTailwindMerge } from "tailwind-merge";

/**
 * Class merging for the app's own code. Like Untitled UI's cx (same extra text
 * sizes), except that a font size no longer cancels a line height: every size
 * here is an arbitrary `text-[…rem]` that sets the font size only, so
 * cx("text-[1.25rem] leading-[1.1]", "text-[0.875rem]") keeps leading-[1.1], as
 * the old CSS did.
 */
export const cx = extendTailwindMerge({
  extend: { theme: { text: ["display-xs", "display-sm", "display-md", "display-lg", "display-xl", "display-2xl"] } },
  override: { conflictingClassGroups: { "font-size": [] } },
});

/** A block: card surface, border, 12px corners, 24px padding, card shadow; plain in print. */
export const CARD =
  "card rounded-[12px] border border-primary bg-primary p-6 [box-shadow:var(--shadow-card)] " +
  "print:mb-[12pt] print:break-inside-avoid print:border-[#888]! print:bg-white! print:shadow-none!";

/** A small title inside a card. */
export const SECTION_TITLE = "mt-0 mb-4 text-[0.875rem] font-medium text-primary";

/** Secondary small text. */
export const CAPTION = "text-[0.75rem] text-tertiary";

/** Ids and commands. */
export const MONO = "font-mono text-[0.75rem] break-all";

/** "Nothing here" inside a block. */
export const EMPTY_STATE = "py-6 text-center text-[0.875rem] text-tertiary";

/** A row of controls. */
export const FORM_ROW = "flex flex-wrap items-center gap-2";

/** A label above its field. */
export const FORM_FIELD = "flex flex-col gap-1 text-[0.875rem] [&>span:first-of-type]:font-medium";

/** Filters above a list. */
export const FILTER_BAR = "flex flex-wrap items-center gap-2";
export const FILTER_SEP = "h-[22px] w-px bg-(--border)";

/** A key / value list with lines between rows. */
export const KV_LIST =
  "m-0 grid list-none gap-2 p-0 " +
  "[&>li]:flex [&>li]:items-center [&>li]:justify-between [&>li]:border-b [&>li]:border-secondary [&>li]:py-2 [&>li:last-child]:border-b-0";

/**
 * A compact table: quiet header, 10px 12px cells, thin lines, row hover.
 * Rows that open something take TABLE_ROW_CLICKABLE; the chosen one TABLE_ROW_SELECTED.
 */
export const TABLE_COMPACT =
  "table-compact w-full border-collapse text-[0.8125rem] " +
  "[&_th]:border-b [&_th]:border-secondary [&_th]:bg-secondary [&_th]:px-3 [&_th]:py-2.5 [&_th]:text-left [&_th]:text-[0.75rem] [&_th]:font-semibold [&_th]:whitespace-nowrap [&_th]:text-quaternary " +
  "[&_td]:border-b [&_td]:border-secondary [&_td]:px-3 [&_td]:py-2.5 [&_td]:align-middle " +
  "[&_tbody_tr:hover_td]:bg-primary_hover [&_tbody_tr:last-child_td]:border-b-0";
export const TABLE_ROW_CLICKABLE =
  "is-clickable cursor-pointer focus-visible:shadow-none focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-(--border-focus)";
export const TABLE_ROW_SELECTED = "is-selected [&>td]:bg-(--chip-active-bg)!";

/** A table wider than its box scrolls sideways, with a shadow on the edge that has more. */
export const TABLE_SCROLL =
  "table-scroll overflow-x-auto " +
  "[background:linear-gradient(to_right,var(--bg-card)_30%,transparent)_left/40px_100%_no-repeat_local,linear-gradient(to_left,var(--bg-card)_30%,transparent)_right/40px_100%_no-repeat_local,radial-gradient(farthest-side_at_0_50%,color-mix(in_srgb,var(--text-primary)_22%,transparent),transparent)_left/16px_100%_no-repeat_scroll,radial-gradient(farthest-side_at_100%_50%,color-mix(in_srgb,var(--text-primary)_22%,transparent),transparent)_right/16px_100%_no-repeat_scroll]";

/** Under a table: counts and "show more". */
export const TABLE_FOOT =
  "flex items-center justify-between gap-2 border-t border-secondary px-4 py-2 text-[0.75rem] text-tertiary";

/** Two lines in one cell. */
export const CELL_STACK = "flex min-w-0 flex-col gap-0.5";
export const CELL_SUB = "text-[0.6875rem] text-quaternary";
export const CELL_PRINCIPAL = "min-w-40 max-w-[260px] [overflow-wrap:anywhere]";
export const CELL_ROLE = "min-w-30 max-w-[220px] [overflow-wrap:anywhere]";

/** A number in a table: right-aligned, quiet, same-width digits. */
export const NUM = "text-right text-tertiary tabular-nums";

/** A small pill (state, level). Colours come from one of the maps below. */
export const BADGE = "badge inline-block rounded-full px-2 py-0.5 text-[0.75rem] font-medium normal-case tracking-normal print:border print:border-[#555]! print:bg-white! print:text-black!";

const SUBTLE = {
  red: "bg-(--accent-red-subtle) text-error-primary",
  orange: "bg-(--accent-orange-subtle) text-warning-primary",
  green: "bg-(--accent-green-subtle) text-success-primary",
  blue: "bg-(--accent-blue-subtle) text-(--accent-blue)",
  purple: "bg-(--accent-purple-subtle) text-(--accent-purple)",
  grey: "bg-primary_hover text-tertiary",
} as const;

/** Badge colours by meaning. */
export const BADGE_TONE = {
  pending: SUBTLE.orange,
  running: SUBTLE.blue,
  done: SUBTLE.green,
  failed: SUBTLE.red,
  custom: SUBTLE.orange,
  yours: SUBTLE.blue,
  sevHigh: SUBTLE.red,
  sevMedium: SUBTLE.orange,
  sevLow: SUBTLE.grey,
} as const;

/** A place's level as a badge colour (management group, subscription…). */
export const BADGE_LEVEL: Record<string, string> = {
  mgmt_group: SUBTLE.blue,
  subscription: SUBTLE.green,
  resource_group: SUBTLE.orange,
  resource: SUBTLE.grey,
};

/** A tiny tag beside a name (R, C, PIM…). */
export const TAG =
  "tag inline-flex h-4 min-w-4 items-center justify-center rounded-[2px] px-1 align-middle text-[0.625rem] font-bold";
export const TAG_TONE = {
  risk: SUBTLE.red,
  custom: SUBTLE.orange,
  unresolved: SUBTLE.purple,
  condition: SUBTLE.green,
  pim: SUBTLE.blue,
  conditionOther: SUBTLE.orange,
} as const;

/** The level of a place as an outlined tag (MG, Sub, RG). */
export const LEVEL_TAG =
  "level-tag flex-none rounded-[2px] border border-secondary px-[5px] text-[0.6875rem] leading-[1.5] font-semibold text-tertiary print:[print-color-adjust:exact]";

/** Severity dot. */
export const SEV_DOT = "sev-dot inline-block size-2 flex-[0_0_8px] rounded-full";
export const SEV_DOT_TONE: Record<string, string> = {
  high: "bg-(--accent-red)",
  medium: "bg-(--accent-orange)",
  low: "bg-(--text-muted)",
};

/**
 * The five capability levels' accent, as the --who-accent variable the old
 * .who-* classes set (who can change access, keys, change, data, view).
 */
export const WHO_ACCENT: Record<string, string> = {
  access: "[--who-accent:var(--accent-red)]",
  keys: "[--who-accent:var(--accent-orange)]",
  change: "[--who-accent:var(--accent-blue)]",
  data: "[--who-accent:var(--accent-purple)]",
  view: "[--who-accent:var(--text-secondary)]",
  unknown: "[--who-accent:var(--accent-pink)]",
};

/** A capability level in words, with a dot in its colour (put WHO_ACCENT[level] beside it). */
export const LEVEL_PILL =
  "level-pill inline-flex items-center gap-1 font-medium text-(--who-accent,var(--text-secondary)) " +
  "before:size-2 before:rounded-full before:bg-(--who-accent,var(--text-secondary)) before:content-['']";
