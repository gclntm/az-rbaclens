import { ChevronRight } from "@untitledui/icons";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { CARD, cx } from "@/components/classes";
import { NAV_ICONS, type NavIconName } from "@/components/NavIcon";
import { Badge } from "@/ui/untitled/components/base/badges/badges";
import { FeaturedIcon } from "@/ui/untitled/components/foundations/featured-icon/featured-icon";

/**
 * The building blocks of a page: page header, breadcrumbs, status tiles,
 * section cards and state pills, on Untitled UI (docs/design.md).
 *
 * Colour always says a state, never decorates:
 * ok (green) = up to date or done · warn (amber) = needs attention ·
 * danger (red) = failed or about to lose something · info (blue) = neutral.
 *
 * A page reads top to bottom: title and one sentence, up to three status tiles
 * ("is everything fine?"), a banner only when something needs doing, then
 * sections, each a card with an icon, a title and one line of explanation.
 *
 * These parts take children from pages still on the old CSS, so they are not
 * .uui regions: every element sets its own margins, sizes and colours.
 * status-tile, tile-label and tone-* stay on the tiles as hooks for tests.
 */
export type Tone = "ok" | "warn" | "danger" | "info";

/** A tone in Untitled UI's colours: the featured icon, a badge, and a value's text. */
const TONE: Record<Tone | "none", { icon: "success" | "warning" | "error" | "brand" | "gray"; text: string }> = {
  ok: { icon: "success", text: "text-success-primary" },
  warn: { icon: "warning", text: "text-warning-primary" },
  danger: { icon: "error", text: "text-error-primary" },
  info: { icon: "brand", text: "text-primary" },
  none: { icon: "gray", text: "text-secondary" },
};

const BADGE: Record<Tone | "none", "success" | "warning" | "error" | "brand" | "gray"> = {
  ok: "success",
  warn: "warning",
  danger: "error",
  info: "brand",
  none: "gray",
};

const FOCUS = "outline-focus-ring focus-visible:outline-2 focus-visible:outline-offset-2";

/** Print the tints too (browsers drop backgrounds on paper by default). */
const PRINT_EXACT_INSIDE = "print:[&_*]:[print-color-adjust:exact]";

/**
 * The top of a page: its title (with optional badges beside it), one line saying
 * what it is, actions on the right, and any extra lines (``children``) below.
 * ``back`` puts a link to the page it came from above the title.
 */
export function PageHeader({
  className,
  back,
  title,
  badges,
  sub,
  actions,
  children,
}: {
  /** For the page's layout or print (no-print), not its look. */
  className?: string;
  back?: { to: string; label: string };
  title: ReactNode;
  badges?: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className={cx("flex flex-wrap items-end justify-between gap-4", className)}>
      <div className="min-w-0 flex-1">
        {back && (
          <Link
            to={back.to}
            className={`mb-1 inline-block rounded-md text-sm font-medium text-tertiary no-underline hover:text-secondary hover:underline ${FOCUS}`}
          >
            ← {back.label}
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="m-0 text-display-xs font-semibold tracking-tight text-primary">{title}</h1>
          {badges}
        </div>
        {sub && <div className="mt-1 text-md text-tertiary">{sub}</div>}
        {children}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

/** Where the page sits: links to its parents, then the page itself (the last item, not a link). */
export function Breadcrumbs({ items }: { items: { label: ReactNode; to?: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="uui flex flex-wrap items-center gap-1 text-sm">
      {items.map((item, i) => {
        const last = i === items.length - 1;
        return (
          <Fragment key={i}>
            {i > 0 && <ChevronRight aria-hidden="true" className="size-4 flex-none text-fg-quaternary" />}
            {last || !item.to ? (
              <span aria-current={last ? "page" : undefined} className="px-1 font-semibold text-secondary">
                {item.label}
              </span>
            ) : (
              <Link
                to={item.to}
                className={`rounded-md px-1 font-medium text-tertiary hover:text-secondary hover:underline ${FOCUS}`}
              >
                {item.label}
              </Link>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

/** A row of up to three (four at most) status tiles. */
export function StatusTiles({ children }: { children: ReactNode }) {
  return <section className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(210px,1fr))] gap-4">{children}</section>;
}

/**
 * One answer at a glance: what it is about, its state in two or three words, one line of detail.
 * The state shows in the icon and the value's colour. With ``to`` the whole tile is a link to
 * where the answer is worked out.
 */
export function StatusTile({
  tone,
  icon,
  label,
  value,
  note,
  to,
}: {
  tone: Tone;
  icon: NavIconName;
  label: string;
  value: ReactNode;
  note?: ReactNode;
  to?: string;
}) {
  const t = TONE[tone];
  const body = (
    <>
      <div className="flex items-center gap-3">
        <FeaturedIcon icon={NAV_ICONS[icon]} color={t.icon} theme="light" size="md" />
        <span className="tile-label text-sm font-medium text-tertiary">{label}</span>
      </div>
      <div className={`mt-3 text-display-xs font-semibold [overflow-wrap:anywhere] ${t.text}`}>{value}</div>
      {note && <div className="mt-1 text-sm text-tertiary [overflow-wrap:anywhere]">{note}</div>}
    </>
  );
  // CARD gives every block the same border, radius and shadow; tone-* stays as a hook for tests.
  const box = cx(
    CARD,
    `status-tile tone-${tone}`,
    "flex min-w-0 flex-col p-5 print:[print-color-adjust:exact]",
    PRINT_EXACT_INSIDE,
    to && `text-primary no-underline transition duration-100 ease-linear hover:border-brand hover:bg-primary_hover ${FOCUS}`,
  );
  return to ? (
    <Link to={to} className={box}>
      {body}
    </Link>
  ) : (
    <div className={box}>{body}</div>
  );
}

/**
 * A block's header: an icon in the block's state colour, a title, one line saying
 * what it is, optional actions on the right. SectionCard uses it; a page that
 * builds its own block (the Role guide's list) uses it alone.
 */
export function SectionHeader({
  icon,
  tone = "info",
  title,
  hint,
  actions,
}: {
  icon: NavIconName;
  tone?: Tone;
  title: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={cx("flex items-start gap-3 border-b border-secondary bg-primary px-6 py-4", PRINT_EXACT_INSIDE)}>
      <FeaturedIcon icon={NAV_ICONS[icon]} color={TONE[tone].icon} theme="light" size="md" />
      <div className="min-w-0 flex-1">
        <h2 className="m-0 text-lg font-semibold text-primary">{title}</h2>
        {hint && <p className="m-0 mt-0.5 text-sm text-tertiary">{hint}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center justify-end gap-2 self-center">{actions}</div>}
    </header>
  );
}

/**
 * A block of a page: a header (icon in the block's state colour, title, one line
 * saying what it is, optional actions on the right), then its body.
 */
export function SectionCard({
  id,
  className,
  icon,
  tone = "info",
  title,
  hint,
  actions,
  children,
}: {
  id?: string;
  /** For the page's layout (where the block sits), not its look. */
  className?: string;
  icon: NavIconName;
  tone?: Tone;
  title: string;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      className={cx(CARD, "mb-4 overflow-clip p-0", className)}
      id={id}
    >
      <SectionHeader icon={icon} tone={tone} title={title} hint={hint} actions={actions} />
      <div className="px-6 pt-4 pb-6">{children}</div>
    </section>
  );
}

/** A state in a word or two, coloured by its tone. */
export function StatePill({ tone, children }: { tone: Tone | "none"; children: ReactNode }) {
  return (
    <span className={cx("uui inline-flex align-middle", PRINT_EXACT_INSIDE)}>
      <Badge type="pill-color" size="sm" color={BADGE[tone]}>
        {children}
      </Badge>
    </span>
  );
}
