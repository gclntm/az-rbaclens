/**
 * Whether a side-menu item is the page being shown, matched like
 * react-router's NavLink: the item's path or anything below it
 * (/places covers /places/sub/…), or only the exact path with ``end``.
 */
export function isCurrent(pathname: string, to: string, end = false): boolean {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (end || to === "/") return path === to;
  return path === to || path.startsWith(`${to}/`);
}

/** A count in the menu: "999+" past 999. */
export function countText(n: number): string {
  return n > 999 ? "999+" : String(n);
}
