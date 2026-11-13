# Design system

How RBAC Lens looks and how its UI is built: Untitled UI React (the free MIT
components) on Tailwind CSS 4, with our own colours, in a light and a dark theme.
History and decisions: [plan-untitled-ui.md](plan-untitled-ui.md).

---

## Contents

- [Principles](#principles)
- [Stack and files](#stack-and-files)
- [Colours and themes](#colours-and-themes)
- [Type, spacing, radius, shadow](#type-spacing-radius-shadow)
- [Building blocks](#building-blocks)
- [Writing styles](#writing-styles)
- [Accessibility](#accessibility)
- [Print](#print)
- [Checking a change](#checking-a-change)

---

## Principles

- **Colour says a state, never decorates.** ok (green) = up to date or done ·
  warn (amber) = needs attention · danger (red) = failed or about to lose
  something · info (blue) = neutral. The five capability levels have their own
  accents (change access red, keys orange, change resources blue, data purple,
  view grey).
- **A page reads top to bottom:** title and one sentence, up to three status
  tiles, a banner only when something needs doing, then sections (cards with an
  icon, a title and one line of explanation).
- **Both themes always.** Every colour comes from a token that switches with the
  theme; nothing is hardcoded.

---

## Stack and files

| What | Where |
|------|-------|
| Tailwind CSS 4 (configured in CSS, no config file) | `frontend/src/styles/index.css` |
| Untitled UI React, copied as source (MIT only, never PRO) | `frontend/src/ui/untitled/` ([its README](../frontend/src/ui/untitled/README.md)) |
| Untitled UI's tokens | `frontend/src/ui/untitled/styles/theme.css` (upstream, unchanged) |
| Their tokens mapped onto our colours | `frontend/src/styles/tokens.css` |
| Our variables and element defaults | `frontend/src/styles/elements.css` |
| Shared class strings (cards, tables, badges…) | `frontend/src/components/classes.ts` |
| Icons | `@untitledui/icons`, by name in `frontend/src/components/NavIcon.tsx` |
| Charts | `recharts` through Untitled UI's chart pieces (`components/charts/`) |

**Cascade layers** (`index.css`), lowest first:

1. `theme`: Tailwind's and Untitled UI's tokens.
2. `base`: Tailwind's reset (preflight), for every element.
3. `elements`: our variables per theme, page font, links, focus ring, form
   fields, print paper (`elements.css`).
4. `utilities`: Tailwind utilities, the only way markup is styled.

There is no other CSS, apart from the report's `@page` rule
(`pages/report/report.css`). Tailwind scans every file under `frontend/src`.

---

## Colours and themes

`ThemeProvider` (`frontend/src/theme/ThemeContext.tsx`) resolves Light, Dark or
System (the default, following `prefers-color-scheme`) and sets both
`data-theme` and the `dark-mode` class on `<html>`. The choice is kept in
`localStorage` under `azure-dash-theme`. The side menu's button cycles
Auto → Light → Dark.

Our variables live in `elements.css`, per theme; `tokens.css` points Untitled
UI's semantic tokens at them, so Untitled UI components and our utilities use the
same colours in both themes.

| Use | Utility | Variable | Light | Dark |
|-----|---------|----------|-------|------|
| Page background | `bg-(--bg-root)` | `--bg-root` | `#e6ebf2` | `#080b14` |
| Card / block | `bg-primary` | `--bg-card` | `#ffffff` | `#1b243a` |
| Hover | `bg-primary_hover` | `--bg-card-hover` | `#f8fafc` | `#212d45` |
| Table header | `bg-secondary` | `--bg-table-head` | `#eef2f7` | `#141c2f` |
| Card border | `border-primary` | `--border-card` | `#b9c4d3` | `#33466a` |
| Lines inside a block | `border-secondary` | `--border` | `#e2e8f0` | `#1e2d45` |
| Main text | `text-primary` | `--text-primary` | `#0f172a` | `#f1f5f9` |
| Secondary text | `text-tertiary` | `--text-secondary` | `#475569` | `#94a3b8` |
| Quiet text | `text-quaternary` | `--text-muted` | `#5b6779` | `#8a9aae` |
| Links | `text-brand-secondary` | `--link` | `#1d4ed8` | `#93c5fd` |
| Chosen item (menu, pills, rows) | `bg-(--chip-active-bg)` | `--chip-active-bg` | `#dbeafe` | `#1e3a5f` |

Mind the names: Untitled UI's `text-secondary` is *not* our secondary grey; use
`text-tertiary`.

**State colours** (text keeps 4.5:1 on our surfaces in both themes):

| State | Text | Background tint | Light | Dark |
|-------|------|-----------------|-------|------|
| ok | `text-success-primary` | `bg-(--accent-green-subtle)` | `#047857` | `#34d399` |
| warn | `text-warning-primary` | `bg-(--accent-orange-subtle)` | `#a85007` | `#fbbf24` |
| danger | `text-error-primary` | `bg-(--accent-red-subtle)` | `#b91c1c` | `#f87171` |
| info | `text-(--accent-blue)` | `bg-(--accent-blue-subtle)` | `#1d4ed8` | `#60a5fa` |

Purple (`--accent-purple`) marks data access and unresolved identities; pink is
the unknown capability. Text on a solid accent uses `text-(--on-accent)`.

**Brand:** Untitled UI's `brand` scale is our blue (`#eff6ff` … `#172554`,
600 = `#2563eb`). The primary button is `#2563eb` with white text in both themes.

---

## Type, spacing, radius, shadow

- **Fonts:** Inter for text, JetBrains Mono for ids, commands and code, both
  served by the app (`@fontsource`, no Google request). `font-mono` is JetBrains
  Mono.
- **Base size** 16px; body text 14px (`0.875rem`), line height 1.5 (1.65 in dark).
- **Font sizes in our markup are arbitrary values** (`text-[0.75rem]`), never
  `text-xs` / `text-sm`, because Tailwind's named sizes also set a line height.
  Common sizes: page title 24px (`text-display-xs`, Untitled UI), section title
  18px, body 14px (`0.875rem`), table 13px (`0.8125rem`), caption 12px
  (`0.75rem`), micro 11px (`0.6875rem`).
- **Spacing** on the 4px grid: `1` = 4px, `2` = 8px, `4` = 16px, `6` = 24px
  (card padding), `8` = 32px (page padding).
- **Radius:** cards 12px (`rounded-[12px]`), buttons and fields 8px, pills and
  badges full.
- **Shadow:** cards use the card shadow (`[box-shadow:var(--shadow-card)]`);
  panels and dialogs Untitled UI's `shadow-xl`.

---

## Building blocks

Use these before writing new classes.

| Need | Use |
|------|-----|
| Page title, one line, actions, breadcrumbs | `PageHeader`, `Breadcrumbs` (`components/PageTemplate.tsx`) |
| Status tiles | `StatusTiles` + `StatusTile` (tone, icon, label, value, note) |
| A block | `SectionCard` (icon, tone, title, hint, actions); `SectionHeader` alone |
| A state in words | `StatePill` (Untitled UI badge) |
| Buttons | `Button` (`components/Button.tsx`): primary, secondary, tertiary, danger; `to` for links; `buttonClass()` for a plain `<a>` |
| Filter pills, link buttons, Copy | `pillClass`, `linkButtonClass`, `copyButtonClass`, `decisionClass` (`components/chips.ts`) |
| Section tabs | `TabNav` + `tabClass` (links, not ARIA tabs) |
| Side panel | `Drawer` (focus kept inside, Escape and a click beside it close) |
| "Are you sure?" | `useConfirm()` (`components/ConfirmDialog.tsx`), never `window.confirm` |
| Messages | `useToast()` (errors stay until dismissed) |
| Changes per week | `LazyWeekChart` (recharts, loaded on its own) |
| Cards, tables, badges, tags, captions… | the strings in `components/classes.ts` (`CARD`, `TABLE_COMPACT`, `TABLE_SCROLL`, `BADGE` + `BADGE_TONE` / `BADGE_LEVEL`, `TAG`, `SEV_DOT`, `WHO_ACCENT`, `CAPTION`, `MONO`, `EMPTY_STATE`…) |
| Icons | `NavIcon` by name, or `NAV_ICONS[name]` for Untitled UI parts |

Untitled UI components used directly: the side menu's nav item, badges, button,
featured icon and chart base. Anything else from the free repo is copied into
`frontend/src/ui/untitled/` unchanged, with only its import paths rewritten.

Tables stay native `<table>` elements with `TABLE_COMPACT` (Tab moves link by
link; a table wider than its box scrolls inside `TABLE_SCROLL`, with a shadow on
the edge that has more).

---

## Writing styles

- **Tailwind utilities only**, on Untitled UI's tokens. No new CSS files, no
  `@apply`, no inline `style` for colours or spacing.
- **Merge with `cx` from `components/classes.ts`.** It is tailwind-merge with one
  change: a font size does not cancel a line height. Later classes win.
- **Never build a class name from a template string** (`badge-${level}`):
  Tailwind only sees literal strings. Use a `Record` of full class strings
  (`BADGE_LEVEL`, `WHO_ACCENT`, `SEV_DOT_TONE`…).
- **No hardcoded colours.** Use the utilities above or `prop-(--variable)`; a new
  colour is a new variable in both themes in `elements.css`.
- **Inside Untitled UI parts** (React Aria elements, `.uui` regions) links inherit
  their colour; in page content they are underlined in `--link`.
- **Breakpoints:** the side menu becomes a top bar at 860px (`max-[860px]:`);
  pages use `max-[720px]:` and similar where needed.
- **Old class names on elements are inert hooks** (`card`, `badge`, `status-tile`,
  `tile-label`, `tone-*`, `page-content`): tests or print rules select them. They
  carry no style; don't style through them.

---

## Accessibility

| Requirement | How |
|-------------|-----|
| Focus | One visible ring: `--focus-ring` shadow in page content, a 2px outline in Untitled UI parts (`elements.css`). |
| Targets | 44px for the theme and close buttons; 36px for menu items, buttons and fields (Untitled UI's small size). |
| Contrast | 4.5:1 for text in both themes; state colours are our accents, not Untitled UI's lighter ones. |
| Motion | `prefers-reduced-motion` stops animations and transitions; panels use `motion-reduce:animate-none`. |
| Dialogs | `Drawer` and `useConfirm` move focus in, keep Tab inside, close on Escape, give focus back. |
| Names | Icons are `aria-hidden`; counts carry screen-reader text; charts list each week in words. |
| Colour scheme | `<meta name="color-scheme" content="light dark">`. |

---

## Print

The report and the People page print. In print the page is white with black
text (`elements.css`), the side menu, buttons and tabs are hidden
(`print:hidden`), cards get a grey border and no shadow (`CARD`), and the report
has its own print-only layout (`ReportPrint.tsx`, `print:` utilities) with A4
pages and a footer in the page margin (`report.css`).

---

## Checking a change

- `make check`: lint, tests, type check.
- **Both themes, computer and phone width (375px).**
- **A change meant to keep the look:** compare the page element by element
  against the previous build, and print the report and the People page to PDF.
  Phase 7 of the plan describes the method (computed styles keyed by DOM path,
  and comparing PDF content streams).
