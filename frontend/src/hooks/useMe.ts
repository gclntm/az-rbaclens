import { useEffect, useState } from "react";

import { api, type Me } from "@/api/client";

let cached: Promise<Me> | null = null;

/** Who is using the app (GET /me), fetched once per page load. */
export function useMe(): Me | null {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => {
    let alive = true;
    cached ??= api.me().catch(() => {
      cached = null;
      return { signed_in: false, oid: null, name: null, is_admin: false, responsible_for: [] };
    });
    cached.then((m) => alive && setMe(m));
    return () => {
      alive = false;
    };
  }, []);
  return me;
}

/** "prod", "prod and data-lake", "prod, data-lake and 2 more". */
export function placesText(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  if (names.length === 3) return `${names[0]}, ${names[1]} and ${names[2]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

/** Signed in and not an admin: sees only their places and their own access. */
export function isRestricted(me: Me | null): boolean {
  return !!me && me.signed_in && !me.is_admin;
}
