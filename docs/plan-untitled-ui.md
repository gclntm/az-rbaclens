# Plan: Untitled UI for the interface, with theme switching

Status: **complete.** All phases (0 to 7) released: phases 0 to 6 in 0.30.0, phase 7 (old CSS removed, global reset on) in 0.31.0 (2026-11-13). How the interface is built now: [design.md](design.md). Theme switching confirmed as the meaning of "switching".

**The request:** build the app's interface (the dashboard elements:
navigation, tiles, cards, tables, badges, tabs, drawers, forms, charts)
from [Untitled UI React](https://www.untitledui.com/react), and keep
switching between light, dark and the system theme.

This sits **before phase 5 of [plan-before-github.md](plan-before-github.md)**
(publish). The README screenshots should show the new look, and the
licence questions below belong to the same public/private decision.

---

## 1. What Untitled UI React is (checked 2026-11-12)

| | |
|---|---|
| **Licence** | The open-source components are **MIT** ([repo LICENSE](https://github.com/untitleduico/react)). PRO components are under a separate licence that forbids exposing their source "in any product, including open-source repositories" and names "browser-accessible code". |
| **How it ships** | Source you copy into the project (a CLI, or copy from the docs), not an npm package. Needs npm packages beside it. |
| **Stack** (repo `package.json`) | React `^19.3`, TypeScript `^6.0`, Tailwind CSS `^4.3` (theme in CSS with `@theme`, no config file), React Aria Components `^1.21`, `tailwind-merge`, `tailwindcss-animate`, `tailwindcss-react-aria-components`, `@untitledui/icons`. Charts use `recharts ^3.10`; some components use `motion`. Their repo builds with Vite 8. |
| **Free components in the MIT repo** | Sidebar and header navigation, table, tabs, modals, slide-out panels, pagination, date picker, empty state, loading indicator, file upload, line / bar / pie / radar charts, gauges and progress circles. Base: buttons, button groups, badges, tags, inputs, select, checkbox, radio, toggle, textarea, dropdown, tooltip, avatar, progress, slider. |
| **Not in the free repo** | Metric / stat tiles as such (built from base pieces instead), and the dashboard page examples (PRO). |
| **Dark mode** | The class `dark-mode` on `<html>` swaps CSS variables; Tailwind's `dark:` follows that class. Their Vite starter has a provider that follows the system by default. |
| **Font** | Inter, through the variable `--font-inter`. No external font link, so it fits our CSP once `--font-inter` points at the Inter we already host. |

**Consequence for publishing:** only the MIT components can go into a
public repository. Plan with those only. PRO stays out unless the
repository stays private and someone buys a licence.

## 2. What we have today

- React 19, Vite 7, TypeScript 5.7, React Router 7; no Tailwind.
- About 2,700 lines of hand-written CSS in `src/styles/`, plus page CSS
  (report, advice, changelog…). Colours are tokens in `variables.css`.
- Every page is on `components/PageTemplate.tsx` (status tiles, section
  cards, state pills): one place to change most of the look.
- `theme/ThemeContext.tsx`: light / dark / system, saved under
  `azure-dash-theme`, applied as `data-theme` on `<html>`.
- Fonts: Inter and JetBrains Mono from `@fontsource`, served as files
  (the CSP blocks anything else; see 0.19.1).
- CSP: `style-src 'self' 'unsafe-inline'; font-src 'self'; script-src 'self'`.
  A Tailwind build is a plain CSS file, so it fits.
- `make check` runs ESLint (with jsx-a11y), tsc and vitest; files stay
  under 500 lines.

## 3. Decisions (yours)

1. **How far.** *(a)* Home and the dashboard-like pages only, or *(b)*
   the whole interface, page by page. **Recommended: (b)**, through
   `PageTemplate` first, so the app doesn't end up with two looks.
2. **Brand colour.** Keep today's blue as Untitled UI's brand scale, or
   pick another. Recommended: keep it.
3. **Icons.** Replace our hand-drawn `NavIcon` set with
   `@untitledui/icons`, or keep ours. Recommended: replace, for one style.
4. **Charts.** Add charts where they help: changes per week on Home,
   Activity Log coverage, the report's trend against a saved assessment.
   This adds `recharts` (loaded only on pages that draw a chart).
   Recommended: yes, in phase 5 below, after the rest.
5. **Free only.** Recommended: MIT components only, so phase 5 of the
   GitHub plan can publish them. Revisit only if the repository stays
   private.

## 4. Phases

Each phase is its own release, checked on the test stack in light, dark
and at phone width, **and on the built nginx image** (the CSP only
applies there, and the dev server hid the 0.19.1 font bug).

### Phase 0: a spike, thrown away if it fails (S)

On the test stack, uncommitted on `dev` until it passes:

- Add Tailwind v4 (`tailwindcss`, `@tailwindcss/vite`) and the npm
  packages Untitled UI needs. **Load Tailwind without its preflight reset**
  during the migration, so it doesn't fight `shared.css`'s global
  `input`/`select`/`button` rules (and the other way round). Preflight
  comes back in phase 6, when the old CSS is gone.
- Copy three components in: button, badge, table. They go in
  `src/ui/untitled/` with the MIT notice kept, so their `@/components/…`
  paths don't collide with ours.
- Check:
  - Vite 7 and TypeScript 5.7 build their source, or whether we need
    TypeScript 6 / Vite 8 first;
  - ESLint and jsx-a11y pass on the copied code, or it needs a narrow
    exclusion;
  - the `dark-mode` class works with our toggle;
  - the CSP on the nginx image: no blocked style or font;
  - **bundle size before and after** (0.26.0 was a performance release).
- **Done when** one real page (To do) renders with the three components
  in both themes, `make check` passes, and the size cost is known. If the
  cost or the conflicts are too high, stop and report.

#### Phase 0 results (2026-11-12)

What was tried: Tailwind CSS 4.3.5 and the Untitled UI packages, upstream
commit `207c26b` (2026-11-10). Button, badge and table were copied with what
they import: 19 files, about 2,500 lines, in `src/ui/untitled/`. The To do
page uses the button, the badges, and the table in a new "Table" view next
to the cards.

- **Builds as it is:** Vite 7, TypeScript 5.7 and React 19 compile their
  source unchanged. No upgrade needed first.
- **Lint:** 7 jsx-a11y findings in their checkbox, radio, toggle and a badge
  image (a hint text that stops a click; an alt text wording). Turned off
  for `src/ui/untitled/**` only, with the reason in `eslint.config.js`;
  their code stays exactly as upstream wrote it.
- **Two CSS systems side by side:**
  - All the hand-written CSS is in a `legacy` cascade layer below Tailwind
    (`src/styles/index.css`): the global files, and the page stylesheets
    imported by components (advice, changelog, report, report print).
    Old CSS against old CSS cascades exactly as before; Tailwind utilities
    beat it. The report's `@page` rule stays outside the layer. The few
    `!important` rules (print, reduced motion) now beat Tailwind too, which
    is what they are for.
  - Tailwind's reset stays off.
  - Our variables that share a name with Tailwind or Untitled UI are now
    `--app-*` (fonts, shadows, two radii: 58 references).
  - Tailwind only scans `src/ui/untitled` and the pages listed there: all
    477 utilities in the build come from those files.
- **Pages not on Untitled UI are unchanged.** Prod's 0.28.0 frontend and the
  trial build were run on the same test data. On Home, Report, a grant page,
  a person's page and Settings › Advice, every element's computed styles,
  sizes included, are identical, in light and dark. The report prints to
  the same 5 A4 pages, the same size to the byte; only the timestamps
  differ.
- **To do (Cards, the default view):** "Won't do…", Dismiss (disabled until
  a reason is typed, then a real dismiss) and Cancel work in both themes.
  "Copy removal script" runs; the in-app browser refuses clipboard writes
  for prod's frontend too, so that part was not testable here.
- **Theme switching:** our `ThemeProvider` now also toggles `dark-mode` on
  `<html>`. It switches live from the side menu toggle, saved choices are
  kept, and the Untitled UI components follow.
- **Content-Security-Policy:** no violation on the built nginx image. Fonts
  stay our self-hosted Inter.
- **Bundle size, gzipped:**

  | | JS (main) | CSS |
  |---|---|---|
  | Before | 170 kB | 15 kB |
  | After | 253 kB (+83) | 26 kB (+11) |

  React Aria is most of it: 60 kB for table, button and link; 84 kB with
  checkbox, menu, tooltip. It is the price of their keyboard, focus and
  screen-reader behaviour, and it is shared: later components add little.
  `tailwind-merge` adds 9 kB, the icons 1 kB.
- **For later phases:**
  - Their tables scroll sideways on a phone; ours stack. Phase 4 needs a
    phone layout for tables.
  - Long names (UPNs, app names) need `overflow-wrap` in table cells.
  - Their dark surfaces (near-black) don't match our dark blue cards yet:
    phase 1's token map.
  - The brand colour is still Untitled UI's default.
  - Splitting the bundle by page (React lazy routes) would win back more
    than React Aria costs: today every page is in the main chunk. Worth
    doing in phase 1.

### Phase 1: foundations (M)

- **Theme bridge.** Keep our `ThemeProvider` and its storage key, so
  people keep their choice. It sets `dark-mode` on `<html>` next to
  `data-theme`. No second provider.
- **Tokens.** Map our tokens onto Untitled UI's variables in one file.
  Our meanings must survive in both themes:
  - the five capability colours (change access, keys, change, data, view);
  - advice weights (must / should / could);
  - control statuses (met / partly / not met);
  - finding severities and state pills.
- **Font.** Point `--font-inter` at the Inter we host; keep JetBrains
  Mono for ids and commands.
- **Icons** (if decision 3 is yes): swap `NavIcon` for `@untitledui/icons`.
- **Done when** the app looks the same as today but reads its colours
  from the new tokens, in both themes.

#### Phase 1 results (2026-11-13)

Decisions: the whole interface, our blue as the brand colour, Untitled UI
icons, charts later (phase 5). Phase 0's size cost accepted, with the bundle
split by page to win it back.

- **A scoped reset (new, found in this phase).** Without Tailwind's reset,
  Untitled UI's components fell back on browser defaults:
  - table rows were 126px high instead of 56px (paragraph margins);
  - buttons used Arial with an "outset" border.

  `src/styles/uui-reset.css` applies Tailwind 4.3.5's reset, rule for rule,
  only inside React Aria elements (`[data-rac]`, which every Untitled UI
  control carries) or a `.uui` region. It uses zero specificity, in the
  `base` layer above `legacy`, so our old element-wide rules no longer
  reach in. Links inside those regions are styled by their own classes.
- **Token map** (`src/styles/tokens.css`):
  - **Surfaces, borders and text:** Untitled UI's semantic tokens point at
    our variables, which already switch with `data-theme`. One map covers
    both themes.
  - **Brand scale:** our exact blue values (Tailwind 4's stock blue is a
    slightly different colour).
  - **Links:** "brand" text maps to our link colour, since Untitled UI's
    dark theme turns it grey.
  - **Fonts:** our Inter and JetBrains Mono.
  - **Result:** the To do table card has exactly our card's background and
    border in both themes (`#ffffff` / `#e2e8f0`, `#1b243a` / `#1e2d45`).
    Its text is 6:1 on the dark card and its badges are 8 to 11:1.
  - **Native dark instead:** switching to Untitled UI's own near-black dark
    theme is a change to this one file.
- **Icons:** `NavIcon` keeps its name API and draws `@untitledui/icons`
  (17 icons, stroke 1.75 as before; To do uses `CheckSquare`).
- **Pages load on demand:** all 18 pages are `React.lazy`.
  - **A tab open across a redeploy:** it reloads once on `vite:preloadError`,
    guarded against loops (`src/staleChunks.ts`). Tested by opening a
    page in an old tab after a new build replaced the files.
  - **What Home downloads** (prod 0.28.0 → now, gzip):

    | | JS | CSS |
    |---|---|---|
    | Prod 0.28.0 | 173 kB | 16 kB |
    | Now | 116 kB | 26 kB |

    React Aria (84 kB) loads only with pages that use Untitled UI. Phase 2's
    sidebar will bring it into every page's first load, which takes most of
    this saving back.
- **Pages not on Untitled UI:** unchanged. On Home, Report, a grant page, a
  person's page and Settings › Advice, every element's computed styles and
  sizes, and every icon's size and colour, are identical to prod 0.28.0, in
  light and dark.
- **Also unchanged:** Changelog; a session moving through Changelog,
  Report, a grant page, To do and back (same style hashes as prod); and the
  printed report (the PDF's content streams are identical).
- **Keyboard focus:** one ring on Untitled UI buttons (2px outline, offset
  2px), because their own shadow utilities hide our old focus shadow.
  Elements without those utilities did show both; phase 2 fixes that.
- **Decided (2026-11-13): the dark primary button is Untitled UI's**,
  `#2563eb` with white text (5.2:1), not our old `#60a5fa` with dark text.
  The vendor file stays unchanged. Old `btn-primary` buttons keep their
  colours until phase 3 replaces them (their variables also colour chosen
  decisions, so changing them early would recolour more than buttons).
- **Found, not caused:** "Activity Log" on Data & schedules (a
  `btn-secondary`) has almost no visible frame in light, on prod too.
  Phase 3 replaces those buttons.

### Phase 2: the shell (M)

- Side menu on Untitled UI's sidebar navigation: groups (To do,
  Explore, Admin), counts, the freshness line, sign-in state and the
  theme switch in the footer. On a phone, the same collapsed behaviour
  as today.
- The command palette stays ours, restyled.
- Page header and breadcrumbs: moved to phase 3 (the free repo has
  neither; they belong with `PageTemplate`).

#### Phase 2 results (2026-11-13)

- **Side menu** (`components/SideMenu.tsx`) is built from Untitled UI's nav
  item (`NavItemBase`, vendored unchanged). The composition is ours:
  - the free sidebar layouts hardcode Untitled UI's logo and account card;
  - the account card needs `react-aria` and a hook we don't have.

  Same groups, counts and footer as before:
  - counts are brand badges, red when to-dos are overdue, with the meaning
    in screen-reader text;
  - the current item is our chip blue;
  - the active item is matched like `NavLink` (`components/navUtils.ts`,
    tested).
- **Same size and breakpoint as before:** the 240px column and the 860px
  breakpoint stay. On a phone the menu is still a top bar with one row
  scrolling sideways (not Untitled UI's drawer). The version "reload" and
  the freshness warnings stay visible without opening anything.
- **Row height:** 36px menu items (Untitled UI's), 40px apart, instead of
  44px on a computer and 40px on a phone. The theme button keeps a 44px
  target.
- **Keyboard focus:** our old global focus ring is a box-shadow, which
  stacked on Untitled UI's outline (two rings on menu items). Inside
  Untitled UI regions the reset now swaps it for the outline alone, so
  every focusable element there shows one 2px ring. Elsewhere the old ring
  stays.
- **In-app links:** React Aria's `RouterProvider` (in `App.tsx`) makes
  Untitled UI links navigate without reloading the page. Checked: no new
  document request on a menu click.
- **Footer, restyled:**
  - freshness line, version and theme button on Tailwind;
  - sign-in state on Untitled UI's badge and buttons;
  - "Sign-in off" no longer wraps on a phone.
- **State colours:** Untitled UI's warning, success and error text and
  icon tokens point at our accents, which keep 4.5:1 (their light warning
  orange is about 3.4:1 on white).
- **Command palette:** same behaviour, Untitled UI's modal look (overlay,
  rounded panel, our chip blue for the highlighted result).
- **Old CSS removed:** `styles/sidebar.css` (270 lines) and the palette
  rules in `redesign.css`.
- **Utilities added to `index.css`:** two utilities from Untitled UI's
  `globals.css` that its components use (`transition-inherit-all`,
  `scrollbar-hide`).
- **Not seen in the browser** (checked with sign-in off and one
  version):
  - the signed-in and signed-out footer;
  - a restricted user's menu;
  - the version "reload" state;
  - the palette in light and on a phone.

  These are checked by the type checker and unit tests only.
- **Found while doing it:** Untitled UI's `bg-*` utilities read
  `--background-color-*`, which is resolved on `:root`. A local override
  must set that variable, not `--color-bg-*`.
- **Pages unchanged:** the main column of 12 pages (all but To do) has the
  same computed styles and icon sizes as prod 0.28.0, in light and dark.
  The printed report's PDF content streams are identical.
- **What Home downloads** (gzip):

  | | JS | CSS |
  |---|---|---|
  | Prod 0.28.0 | 173 kB | 16 kB |
  | Phase 1 | 116 kB | 26 kB |
  | Phase 2 | 140 kB | 25 kB |

  React Aria is back in the first load with the menu. The large 75 kB
  chunk is MSAL, which loads only when sign-in is on.

### Phase 3: `PageTemplate` and the shared pieces (M)

- Page header and breadcrumbs (moved from phase 2), built from Untitled UI
  base pieces.

- Status tiles: built from Untitled UI's base pieces, since the free
  repo has no metric tile.
- Section cards, state pills (badges), tabs, buttons, form fields, select,
  toggles, tooltips.
- Drawers (grant, role, group members, event) on slide-out panels;
  confirmations on modals.
- Toasts stay ours, restyled, unless a component replaces them cleanly.
- **Done when** every page uses only these pieces for its frame, and the
  page tests still pass.

#### Phase 3, slice 1 results (2026-11-13): header, breadcrumbs, tiles, cards, pills

- **One place for the look.** `components/PageTemplate.tsx` styles all of
  these with Untitled UI classes:
  - `PageHeader` (title, badges, one line, actions, a back link, extra
    lines below), now used by all 19 pages that had a hand-written header;
  - `Breadcrumbs` (chevrons, used on the place, grant, person and review
    pages);
  - `StatusTile`, `SectionCard` and `StatePill`.

  Untitled UI's featured icon is vendored unchanged.
- **How a state shows now:**
  - **Tiles:** a tinted icon and the value's colour, on a plain card.
    Before, a 5px band and a tinted background.
  - **Section headers:** the tinted icon alone. Before, a tinted band.
  - **Neutral (info) tile values:** no colour. Before, blue.
  - **Where to change it:** one map in `PageTemplate.tsx` (`TONE`).
- **Pills:** Untitled UI's badges. Their amber is Untitled UI's warning
  yellow-orange, a little more yellow than our accent orange.
- **Cards:** every block (`.card`, 34 plain cards and all section cards)
  has Untitled UI's 12px corners. The border colour and shadow stay ours.
- **Dark mode:**
  - featured icons use our subtle tints and accents (`tokens.css`);
  - Untitled UI's own dark featured icons are solid, saturated circles.
- **Not `.uui` regions:** these parts hold old-CSS children (buttons,
  badges, tables), so they set every margin, size and colour themselves.
  Only the breadcrumbs and the pills are `.uui`. Old class names stay as
  hooks for `template.css`, `print.css` and the tests (`tile-label`,
  `status-tile`, `tone-*`).
- **Checked:**
  - **Card contents:** everything inside the section cards on 11 pages
    has the same computed styles and widths as prod 0.28.0. The only
    differences are the intended ones: pills, and the review cards'
    corners.
  - **Keyboard focus:** linked tiles and breadcrumb links show one ring.
  - **Print:** the printed report is identical; on the People page only
    the header's look changed.
  - **Contrast:** tile values are 8 to 14:1 on the dark card.
- **Left for slice 2:**
  - buttons in the headers are still the old `.btn` (the "Activity Log"
    one has almost no frame, as on prod);
  - print hides `.btn`, so Untitled UI buttons will need a print rule.

#### Phase 3, slice 2a results (2026-11-13): buttons

- **`components/Button.tsx`:** our button, drawn with Untitled UI's, taking
  the props pages already used (`type`, `onClick`, `disabled`, `to`,
  `title`, aria). A codemod converted 81 of them in 32 files; nothing else
  in those tags changed.
- **What React Aria does differently, probed with a throwaway test:**
  - **`title`:** dropped. Our wrapper puts it on a wrapping span, which also
    shows it on a disabled button.
  - **Clicks:** stopped before they reach parent elements or `document`.
    Nothing in the app listens for outside clicks today.
  - **Unaffected:** `onClick`, `type`, `form`, `style`, `disabled` and the
    aria attributes behave as before.
- **Links that must stay a plain `<a>`:** the download link and the
  Mailpit inbox (new tab) use `buttonClass()`, Untitled UI's classes.
- **Left as they were:**
  - the coloured keep / remove / ask decision buttons on the grant and
    review pages (class names built from expressions; they belong with the
    decision redesign);
  - small inline `link-btn` and `copy-btn` buttons (with the pills, next).
- **Checked:**
  - **Same buttons as prod on 23 pages:** each page's buttons and
    button-links have the same accessible name, disabled state, type,
    link target, and expanded and pressed state as on prod 0.28.0. To do
    adds only its Cards / Table switch.
  - **Behaviour:** the row menu opens, a link button navigates without
    reloading, and tooltips stay on disabled buttons. Wrapping doesn't
    change any height.
  - **Print:** buttons are hidden, as before. The printed report differs
    only in its "generated at" minute.

#### Phase 3, slice 2b results (2026-11-13): pills and small buttons

- **`components/chips.ts`:** three class helpers in Untitled UI's look.
  Each element keeps its kind (button, a label around a checkbox, or a
  link), so behaviour and tests are unchanged:
  - `pillClass` for the 35 filter and toggle pills (chosen = our chip
    blue);
  - `linkButtonClass` for buttons that read as links;
  - `copyButtonClass` for "Copy" beside ids.
- **Old CSS removed:** `.pill`, `.link-btn` and `.copy-btn`. The class names
  stay as hooks.
- **Checked:** the button inventory on 23 pages is unchanged.

#### Phase 3, slice 2c results (2026-11-13): tabs

- **`components/TabNav.tsx`:** Untitled UI's underline tabs for the place,
  person and Settings pages, with counts as badges (red for problems).
- **Still links in a `<nav>`:** each section has its own address. Not
  React Aria tabs, so the semantics and keyboard behaviour are as before.
- **Bottom rule:** an inset shadow in the card border colour, so the row
  can scroll sideways on a phone. The old rule was almost invisible on the
  grey page.
- **Print:** tabs are hidden (Settings tabs used to print).
- **Old CSS removed:** `.scan-detail-tabs`, `.settings-tabs` and
  `.tab-count`.

#### Phase 3, slice 2d results (2026-11-13): form fields

- **Native fields, new look:** about 80 fields (text, search, number,
  password, email, select, checkbox, radio) in 27 files. Almost none has a
  class; they take their look from the shared element rules, plus
  contextual rules that size them.
- **What changed in those shared rules (`shared.css`):** Untitled UI's "sm"
  input:
  - 36px tall (was 48), 14px text;
  - our card surface and card-border colour, 8px corners, a light shadow;
  - a brand ring on focus;
  - checkboxes and radios in the brand colour.

  Number, password and email fields now get it too; before they had none.
- **Contextual rules:** `.search-input`, `.form-field input`,
  `.form-grid` fields and `.filter-select` keep only their sizing and
  font.
- **Why not Untitled UI's Input and Select components:** they are React
  Aria compositions (the Select is a list box in a popover). They would
  change behaviour and tests for no visible gain here. Phase 6 moves these
  rules into a base style when the old CSS goes.

#### Phase 3, slice 3 results (2026-11-13): side panels, confirmations, toasts

- **Side panels:**
  - **Look:** the shared `Drawer` (role, group members, event) and the grant
    panel on a place page have Untitled UI's slide-out look: a blurred
    overlay, a panel with a large shadow, a sticky header and an × button
    (`components/CloseButton.tsx`). They slide in, except with reduced
    motion.
  - **Behaviour unchanged:** focus moves in, Tab stays inside, Escape or a
    click beside it closes, and focus returns. The focus handling is still
    ours (`useDialogFocus`, tested). Untitled UI's slide-out is built on
    React Aria's modal, which would have replaced tested behaviour for no
    visible gain.
- **Confirmations:**
  - **`components/ConfirmDialog.tsx`:** `useConfirm()` replaces the four
    `window.confirm()` calls (retention, VACUUM FULL, cleanup of checks,
    deleting saved credentials). It is an `alertdialog` in Untitled UI's
    modal look, with a warning or danger icon, a title, one line, Cancel,
    and the action (red for deletions).
  - **Behaviour:** focus starts on Cancel. Escape or a click beside it
    cancels. Tested.
  - **`Button`:** a `danger` variant (Untitled UI's `primary-destructive`).
  - **`useDialogFocus`:** treats an `alertdialog` like a `dialog` when
    another dialog has the keyboard.
- **Toasts:** Untitled UI's notification look (icon by kind, message,
  action, ×). Same roles (`alert` for errors, which stay until dismissed;
  `status` otherwise) and timing.
- **Checked in the browser:**
  - **Role panel:** focus starts on ×, and Escape returns it to the role
    link.
  - **Confirmation:** a cancelled cleanup sends no request, and focus
    returns to "Run cleanup".
  - **Toast:** an error toast shows with its Dismiss button.
  - **Grant panel:** opens with focus inside, and Escape closes it.
- **Old CSS removed:** the drawer shell rules and the toast rules.

### Phase 4: tables and pages (L)

Page by page, each one checked before the next:

1. To do, Reviews, Needs a look (tables, filters, pagination).
2. People & apps, a person's page (map view, groups, activity).
3. Places, a place's page (all grants, who can do what, changes).
4. Data & schedules, Changes, Activity Log.
5. Settings (all tabs), Role guide.

#### Phase 4, tables results (2026-11-13)

- **Approach:** the shared table rules (`.table-compact`, `.table`) take
  Untitled UI's look. The tables stay native and keep our padding.
  - **The look:** a 12px semibold header in sentence case, in our quietest
    grey, on the table-head surface; thin row lines; a hover; no zebra
    stripes on the Database and Audit tables.
  - **Why not React Aria's table:** moving 25 tables to it would change
    keyboard behaviour (arrow keys inside the table) and the semantics
    the tests rely on.
- **To do's table is a native table again.** The phase 0 React Aria table
  took one Tab stop and moved by arrow keys; every other table goes link
  by link. Untitled UI's badges stay in its cells. It now fits at 1280px:
  926px in a 926px box, where before it was 983px and the Due column was
  clipped.
- **Scroll cue:** a table wider than its box scrolls sideways, as on prod,
  and now a shadow shows on the edge with more behind it. The shadow is
  in the text colour, so it shows in both themes. On a phone the tables
  scrolled the same way on prod (People: 751px in a 293px box), so the
  earlier "not releasable on phones" was overstated.
- **Legacy badges:** level, state and "yours" badges get Untitled UI's pill
  shape and keep their colours, which carry meanings.
- **Decision buttons:** keep / remove / change / ask on the grant and
  review pages, via `decisionClass` in `chips.ts`. A secondary button,
  filled with the decision's colour once chosen; the advice's suggestion
  has a dashed outline. Tested; not seen in the browser, because the test
  data has no open review.
- **Checked against prod 0.28.0, 13 views with tables:**
  - **Content:** the cell text of every table is identical.
  - **At 1280px:** no table overflows that didn't on prod. The place
    page's All grants table already overflowed on prod and is now a little
    narrower (913px vs 927px).
  - **At 1024px:** every table is as wide as on prod or narrower, except
    Audit (+1px). To do scrolls there, with the cue.
  - **Keyboard:** Tab in To do goes Who, Place, Review.
  - **Print:** the printed report is identical.
- **Page sweep:** the Role guide built its own section header with the old
  `section-icon` class, whose CSS slice 1 removed, so its icon showed
  unstyled. It now uses `SectionHeader`, taken out of `SectionCard`. No
  other page builds template markup by hand. Finding cards, the changes
  timeline, the Report page and the place tabs looked right.
- **Not covered:** a person's "What they can do" table (behind its Table
  switch) and a person's activity table (none in the test data).

### Phase 5: charts (S–M, if decision 4 is yes)

- Home: grants given and removed per week.
- Data & schedules: Activity Log coverage per subscription.
- Report: key numbers against a saved assessment.
- `recharts` loaded only on the pages that draw a chart.

#### Phase 5 results (2026-11-13)

- **Released first:** 0.29.0 (phases 0 to 4) went to prod on 2026-11-13.
  Checked on prod's real data, view only:
  - the decision buttons (suggestion outline; chosen button filled, in
    both themes);
  - a person's Table view and activity tables.
- **One chart:** changes per week, as Untitled UI's stacked bar chart on
  `recharts` 3.10.1.
  - `components/charts/WeekChart.tsx`, with Untitled UI's `charts-base`
    vendored unchanged.
  - It replaces the hand-drawn bars on Changes and is new on Home. Home's
    request already carried the weeks, so nothing changed in the backend.
  - Our green and red in both themes, no animation, and each week in
    words for screen readers.
  - **Checked on the test data:** the bars equal the API's `weeks`.
- **Size (gzip):**
  - Home's first load is 142.7 kB, about the same as before.
  - The chart is its own 114 kB chunk (`LazyWeekChart`), downloaded after
    the page has drawn, behind a placeholder of the chart's height.
- **Left as it was:**
  - Data & schedules coverage stays as it is: a bar per subscription is a
    progress bar, not a chart.
  - The Report has no chart: its print layout and the identical-PDF check
    would need their own work, so that is your call.

### Phase 6: clean-up (M)

- Delete the old CSS as pages leave it, then turn Tailwind's preflight
  on.
- Bundle size against the phase 0 baseline; Lighthouse on Home.
- Changelog, and new screenshots for the README (input to phase 5 of
  the GitHub plan).

#### Phase 6 results (2026-11-13): clean-up, without the global reset

- **The global reset is not on, and can't be yet.** Tailwind's preflight
  needs page content off the old classes, and most content still uses
  them:
  - **Above the `legacy` layer** (where `base` sits), it would beat every
    old class rule: card padding, borders, badges.
  - **Below it,** it would shift the browser defaults that old pages rely
    on, and `uui-reset.css` would still be needed.

  Moving page content off the old CSS is a migration of its own, to be
  decided separately.
- **Dead CSS removed:** 12 selectors whose classes appear nowhere in the TS
  or TSX source (comments ignored). Selectors built from expressions are
  kept. Removed: `.btn*`, `.source-card*` and the print rules for `.btn`
  and `.auth-status`, 53 lines in all.
- **A 0.29.0 slip, fixed:** the sign-in screen's button had lost its
  centring when `.btn` went, so it stretched across the card (unseen
  with sign-in off). It is wrapped in
  `.auth-gate-action` again.
- **Unused vendor files removed:** 13 files nothing imports (table,
  avatar, checkbox, dropdown, radio buttons, toggle, tooltip). The ESLint
  exemption for the vendor folder is down to one rule
  (`img-redundant-alt`, for the badge).
- **Proof against the code before phase 6:**
  - **Light, 1280px:** the computed styles of every element on 31 views
    (all pages, tabs, a grant page, the diff page) are identical.
  - **375px:** 17 views are identical.
  - **Print:** the report and People PDFs are identical.
  - **A trap in the method:** a first run differed on 20 views. Run again
    in the same order on both builds, they matched. The difference came
    from the order the probe visited pages in, not from the build.
- **Size (gzip):**

  | | Home first-load JS | CSS | Chart (lazy) |
  |---|---|---|---|
  | Prod 0.28.0 (phase 0 baseline) | 173 kB | 16 kB | — |
  | 0.29.0 | 140 kB | 25 kB | — |
  | Now | 142.8 kB | 27.5 kB (29.2 before phase 6) | 114 kB |

  Lighthouse was not run: the project has no setup for it.
- **Left open:**
  - **`THIRD_PARTY.md`** does not exist yet. It should list Untitled UI,
    its icons, React Aria and recharts with the GitHub publication's
    licence decision.
  - **README screenshots:** they need invented demo data.
  - **Formatting:** the button codemod's multi-line tags are tidied (each
    prop on its own line).

### Phase 7: remove the old CSS (asked 2026-11-13, after 0.30.0)

The request: remove the old CSS and verify that everything is handled
with Untitled UI.

**What "handled with Untitled UI" can mean here.** The free repo has no
component for the place tree, the review, finding and grant cards, or the
report. Those become Tailwind utilities on Untitled UI's tokens. The
result falls into three buckets:
- Untitled UI components;
- utilities on its tokens;
- the plain CSS that has to remain (`@page`, fonts, the token map).

**Gate: nothing should look different from 0.30.0.** This is a change
of mechanism, not a redesign. Two checks:
- **Style probe** (a throwaway script, not kept): headless Chrome over the
  DevTools protocol, with 0.30.0 and the new build side by side. It loads
  33 views fresh and compares about 70 computed properties of every
  element, keyed by DOM path. A self-test (0.30.0 against itself) shows
  0 differences.
- **Orphan scan:** any class in the page that no stylesheet rule
  matches, apart from an allow-list (library classes, `group`, the hooks
  tests use).

**Order:**
1. Move what isn't a class rule (variables, `body`, links, focus, fields)
   out of `legacy`.
2. Decide the shared classes once (helpers like `chips.ts`).
3. Shared components, then pages, one route group per commit; edit
   `className` only, not the markup structure.
4. Global preflight on; `uui-reset.css` goes.
5. The report's print layout last, gated on the PDF.
6. Delete the old files and the `legacy` layer.

**Progress:**
- Baseline image `rbaclens-v030-frontend`. Probe self-test: 0
  differences.
- **Groundwork:**
  - `components/classes.ts`: the shared old classes as class strings.
  - `@source` covers all of `src`; probe: no change.
  - Only 3 old names collide with Tailwind utilities (`sr-only`,
    `table`, `text-secondary`).
- **Six agents** convert disjoint file groups (A components and shell,
  B places, C people, D home / reviews / to-dos / findings / grants /
  changes, E jobs / settings / roles, F report), from one written guide.
- **Markup converted:** 96 files, by six agents with
  disjoint file sets.
  - **Probe, after fixes** (card shadow; line heights tailwind-merge
    dropped):
    - identical in light and dark at 1280 and at 375, apart from load
      timing;
    - report and People PDFs identical.
  - **`cx` from `classes.ts`:** a font size no longer cancels a line
    height.
  - **`elements.css`:** variables and element defaults, in the lowest
    layer.
- **Old stylesheets deleted, then fixed.** The first
  build failed on a stale `advice.css` import and the probe had run on
  the previous image. Rebuilt and confirmed served: identical apart from
  timing.
- **Global preflight:** Tailwind's reset is on for every
  element; `uui-reset.css` is gone.
  - **Explicit classes** in 60 files where the markup relied on browser
    defaults (paragraph margins, lists, bold headings, native control
    metrics).
  - **Side panel:** its large shadow is back.
  - **Report footer:** pinned to `line-height: normal`.
  - **`elements.css`:** white paper in print.

**Result:**
- **CSS left:** `elements.css` (variables and element defaults),
  `tokens.css` (Untitled UI's tokens on our colours), `index.css`
  (layers, sources), Untitled UI's `theme.css`, and `report.css` (`@page`
  only).
- **Untitled UI components used directly:**
  - nav item, badge and button;
  - featured icon;
  - chart base on recharts.

  Everything else is Tailwind utilities on Untitled UI's tokens, mostly
  through `components/classes.ts`.
- **Against 0.30.0** (33 views, light and dark, 1280 and 375): identical,
  except the intended font face of native controls and `code`.
  - Arial and the default monospace become Inter and JetBrains Mono.
  - Effects: Audit cells under 0.2px, Role guide flags 0.16px, and
    narrow dark To do command boxes that wrap one line less.
  - Report and People PDFs identical.
- **Orphan scan:** every class without a rule is a recharts internal,
  Tailwind's `group`, or an inert hook name (tests, state markers).
- **CSS:** 27.5 kB to 25.3 kB gzip.

## 5. What stays custom

These have no Untitled UI equivalent. They get the new tokens and
nothing else:

- the place tree (`ScopeTree`, `TreeLines`, `LevelBar`);
- the access map with its rail;
- the assessment report and its A4 print layout (print stays light,
  whatever the theme).

## 6. Risks

| Risk | Mitigation |
|---|---|
| Two CSS systems fighting during the migration | Old CSS in a `legacy` layer; Tailwind's reset only inside Untitled UI (phase 1's scoped reset) until phase 6; page by page; old CSS deleted as it empties |
| Their source wants TypeScript 6 / Vite 8 / React 19.3 | Found in phase 0; upgrade first, in its own release, if needed |
| Copied code fails our lint, a11y or 500-line rules | Vendor folder `src/ui/untitled/` with a narrow, documented exclusion only where needed |
| Bundle grows (React Aria, recharts, motion) | Measured in phase 0 and 6; charts and heavy parts loaded per page |
| A colour loses its meaning in one theme | Token map in phase 1, checked in both themes on every page |
| Licence | MIT components only; MIT notice kept in the vendor folder and in `THIRD_PARTY.md` |

## 7. Rough size

About 8 to 10 working sessions: phase 0 one, phases 1–3 about four,
phase 4 about three, phases 5–6 about two.
