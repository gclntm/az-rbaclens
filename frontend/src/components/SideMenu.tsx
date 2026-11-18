import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { useLocation } from "react-router-dom";

import { api } from "@/api/client";
import AuthStatus from "@/auth/AuthStatus";
import { PALETTE_HINT } from "@/components/CommandPalette";
import FreshnessLine from "@/components/FreshnessLine";
import { NAV_ICONS, type NavIconName } from "@/components/NavIcon";
import { countText, isCurrent } from "@/components/navUtils";
import VersionTag from "@/components/VersionTag";
import { useActivePolling } from "@/hooks/useActivePolling";
import { isRestricted, useMe } from "@/hooks/useMe";
import { ThemeToggle } from "@/theme/ThemeToggle";
import { NavItemBase } from "@/ui/untitled/components/application/app-navigation/base-components/nav-item";
import { Badge } from "@/ui/untitled/components/base/badges/badges";

const POLL_MS = 5 * 60_000;

/** Open to-dos from the person's reviews (everything with sign-in off). */
function useTodosOpen(): { open: number; overdue: number } {
  const [n, setN] = useState({ open: 0, overdue: 0 });
  const load = useCallback(() => {
    api.todos
      .summary()
      .then((s) => setN({ open: s.mine_open, overdue: s.mine_overdue }))
      .catch(() => setN({ open: 0, overdue: 0 }));
  }, []);
  useEffect(load, [load]);
  useActivePolling(load, POLL_MS, true);
  return n;
}

/** Review items waiting for a decision by the person using the app. */
function useReviewsWaiting(): number {
  const me = useMe();
  const [waiting, setWaiting] = useState(0);
  const mine = !!me?.signed_in;
  const load = useCallback(() => {
    api.reviews
      .list({ mine })
      .then((rounds) => setWaiting(rounds.filter((r) => r.can_decide).reduce((n, r) => n + r.blocking, 0)))
      .catch(() => setWaiting(0));
  }, [mine]);
  useEffect(load, [load]);
  useActivePolling(load, POLL_MS, true);
  return waiting;
}

type ItemSpec = {
  to: string;
  icon: NavIconName;
  label: string;
  end?: boolean;
  count?: number;
  countTitle?: string;
  urgent?: boolean;
};

function Item({ spec, pathname }: { spec: ItemSpec; pathname: string }) {
  const { to, icon, label, end, count, countTitle, urgent } = spec;
  const badge = count ? (
    <span className="ml-3 flex" title={countTitle}>
      <Badge type="pill-color" size="sm" color={urgent ? "error" : "brand"}>
        <span aria-hidden="true">{countText(count)}</span>
        <span className="sr-only">{countTitle ? ` (${count} ${countTitle})` : ` (${count})`}</span>
      </Badge>
    </span>
  ) : undefined;
  return (
    <li className={`py-0.5 max-[860px]:shrink-0 max-[860px]:py-0`}>
      <NavItemBase type="link" href={to} icon={NAV_ICONS[icon]} badge={badge} current={isCurrent(pathname, to, end)}>
        {label}
      </NavItemBase>
    </li>
  );
}

function Group({ label, items, pathname }: { label?: string; items: ItemSpec[]; pathname: string }) {
  return (
    <li className={`max-[860px]:contents`}>
      {label && (
        <p className={`px-2 pt-3 pb-1 text-xs font-semibold tracking-wider text-quaternary uppercase max-[860px]:hidden`}>
          {label}
        </p>
      )}
      <ul aria-label={label} className={`max-[860px]:contents`}>
        {items.map((i) => (
          <Item key={i.to} spec={i} pathname={pathname} />
        ))}
      </ul>
    </li>
  );
}

/**
 * The side menu (Untitled UI nav items), grouped by what people come to do:
 * their to-dos first (reviews, what needs a look, what changed), then
 * exploring (places, people, roles), then admin. Footer: how fresh the data
 * is, sign-in state and theme.
 *
 * The aside is a .uui region (styles/uui-reset.css), so everything in it is
 * styled with Tailwind; it is hidden in print.
 * Narrow screens (max-[860px], as layout.css) get a top bar with one row of
 * items scrolling sideways. Class names are written out in full so Tailwind
 * finds them.
 */
export default function SideMenu() {
  const me = useMe();
  const waiting = useReviewsWaiting();
  const todos = useTodosOpen();
  const admin = !isRestricted(me);
  const nav = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  // On narrow screens the items scroll sideways: keep the current one in view,
  // and fade the edge that has more items behind it.
  const [more, setMore] = useState({ left: false, right: false });
  const measure = useCallback(() => {
    const el = nav.current;
    if (!el) return;
    const left = el.scrollLeft > 4;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setMore((m) => (m.left === left && m.right === right ? m : { left, right }));
  }, []);
  useEffect(() => {
    nav.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "center" });
    measure();
  }, [pathname, measure]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const groups: { label?: string; items: ItemSpec[] }[] = [
    { items: [{ to: "/", end: true, icon: "home", label: "Home" }] },
    {
      label: "To do",
      items: [
        { to: "/reviews", icon: "reviews", label: "Reviews", count: waiting, countTitle: "waiting for a decision" },
        {
          to: "/todos",
          icon: "todo",
          label: "To do",
          count: todos.open,
          countTitle: todos.overdue ? `open, ${todos.overdue} overdue` : "changes to make in Azure",
          urgent: todos.overdue > 0,
        },
        { to: "/findings", icon: "look", label: "Needs a look" },
        { to: "/changes", icon: "changes", label: "Changes" },
      ],
    },
    {
      label: "Explore",
      items: [
        { to: "/places", icon: "places", label: "Places" },
        { to: "/people", icon: "people", label: "People & apps" },
        { to: "/roles", icon: "roles", label: "Role guide" },
        { to: "/report", icon: "report", label: "Report" },
        { to: "/help", icon: "help", label: "How it works" },
      ],
    },
  ];
  if (admin) {
    groups.push({
      label: "Admin",
      items: [
        { to: "/admin/data", icon: "data", label: "Data & schedules" },
        { to: "/settings", icon: "settings", label: "Settings" },
      ],
    });
  }

  const fade = more.left && more.right
    ? "linear-gradient(to right, transparent, #000 48px, #000 calc(100% - 48px), transparent)"
    : more.right
      ? "linear-gradient(to right, #000 calc(100% - 48px), transparent)"
      : more.left
        ? "linear-gradient(to left, #000 calc(100% - 48px), transparent)"
        : undefined;

  return (
    <aside
      className={[
        "uui sticky top-0 flex h-screen flex-col gap-4 overflow-y-auto border-r border-secondary bg-primary px-3 py-5 print:hidden!",
        // The current item (bg-secondary): our chip blue instead of Untitled UI's grey, which barely
        // shows in dark. bg-* utilities read --background-color-*, resolved on :root, so set those here.
        "[--background-color-secondary:var(--chip-active-bg)] [--background-color-secondary_hover:var(--chip-active-bg)]",
        `max-[860px]:z-20 max-[860px]:h-auto max-[860px]:gap-2 max-[860px]:overflow-visible max-[860px]:border-r-0 max-[860px]:border-b max-[860px]:px-4 max-[860px]:py-2`,
      ].join(" ")}
    >
      <div className={`flex flex-col gap-4 max-[860px]:flex-row max-[860px]:items-center max-[860px]:gap-2`}>
        <div className="flex items-center gap-2 px-2 text-primary max-[860px]:px-0">
          <BrandGlyph />
          <span className={`text-md font-bold tracking-tight max-[860px]:hidden`}>RBAC Lens</span>
          <VersionTag />
        </div>
        <button
          type="button"
          className={[
            "flex min-h-10 w-full cursor-pointer items-center justify-between gap-2 rounded-lg bg-primary px-3 py-2 text-left text-sm text-placeholder",
            "shadow-xs ring-1 ring-primary ring-inset outline-focus-ring hover:text-tertiary focus-visible:outline-2 focus-visible:outline-offset-2",
            `max-[860px]:min-w-0 max-[860px]:flex-1`,
          ].join(" ")}
          onClick={() => window.dispatchEvent(new Event("open-command-palette"))}
        >
          <span className="truncate">Search places, people, roles…</span>
          <kbd className="flex-none rounded px-1 font-mono text-xs text-quaternary ring-1 ring-secondary ring-inset">
            {PALETTE_HINT}
          </kbd>
        </button>
      </div>

      <nav
        aria-label="Main"
        ref={nav}
        onScroll={measure}
        style={{ "--fade": fade } as CSSProperties}
        className={`max-[860px]:-mx-4 max-[860px]:scrollbar-hide max-[860px]:overflow-x-auto max-[860px]:px-4 max-[860px]:[mask-image:var(--fade)]`}
      >
        <ul className={`max-[860px]:flex max-[860px]:flex-row max-[860px]:gap-1`}>
          {groups.map((g) => (
            <Group key={g.label ?? "home"} label={g.label} items={g.items} pathname={pathname} />
          ))}
        </ul>
      </nav>

      <div
        className={[
          "mt-auto flex flex-col gap-2 border-t border-secondary pt-4",
          `max-[860px]:mt-0 max-[860px]:flex-row max-[860px]:items-center max-[860px]:justify-between max-[860px]:pt-1`,
        ].join(" ")}
      >
        <FreshnessLine />
        <div className={`flex items-center justify-between gap-2 pl-2 max-[860px]:pl-0`}>
          <AuthStatus />
          <ThemeToggle />
        </div>
      </div>
    </aside>
  );
}

/**
 * RBAC Lens mark: a shield (security) framing an eye (inspection).
 * Stroked with ``currentColor`` so it follows the theme.
 */
function BrandGlyph() {
  return (
    <svg
      className="size-6 flex-none text-fg-brand-primary"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2 L4 5 V11 C4 16 7.5 19.5 12 22 C16.5 19.5 20 16 20 11 V5 Z" />
      <ellipse cx="12" cy="11" rx="4" ry="2.5" />
      <circle cx="12" cy="11" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}
