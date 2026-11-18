import type { CredentialRead, GrantOrigin, SignInRead } from "@/api/client";

/** The Grant page for a grant id (stable across daily checks). */
export function grantHref(grantId: string): string {
  return `/grants/${encodeURIComponent(grantId)}`;
}

export function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** "sp-terraform (app)", "alice@example.com", or a neutral phrase when only the id is known. */
export function givenByLabel(origin: GrantOrigin): string | null {
  if (origin.given_by_name) {
    return origin.given_by_type === "ServicePrincipal" ? `${origin.given_by_name} (app)` : origin.given_by_name;
  }
  return origin.given_by_id ? "an identity RBAC Lens has not named" : null;
}

/**
 * "Since 3 Nov 2026, by sp-terraform (app)" · "Since 3 Nov 2026" · "Given by alice" ·
 * null when Azure recorded nothing.
 */
export function originText(origin: GrantOrigin): string | null {
  const by = givenByLabel(origin);
  if (origin.since) return `Since ${formatDay(origin.since)}${by ? `, by ${by}` : ""}`;
  return by ? `Given by ${by}` : null;
}

/** Whole days between ``iso`` and ``now``, never negative. */
export function daysAgo(iso: string, now: Date = new Date()): number {
  return Math.max(0, Math.floor((now.getTime() - new Date(iso).getTime()) / 86_400_000));
}

export function agoText(iso: string, now: Date = new Date()): string {
  const d = daysAgo(iso, now);
  if (d === 0) return "today";
  if (d === 1) return "yesterday";
  return `${d} days ago`;
}

/**
 * What the Activity Log says about an identity's last change. Azure never
 * records reads, so "no change" is a fact to know, not proof of disuse.
 */
export function lastChangeText(lastActivityAt: string | null | undefined, now: Date = new Date()): string {
  if (!lastActivityAt) return "No change recorded (reads are not recorded)";
  return `Last change ${agoText(lastActivityAt, now)}`;
}

/** "Last signed in 12 days ago", "No sign-in recorded", with "idle" past the threshold. */
export function signInText(s: SignInRead, now: Date = new Date()): string {
  const base = s.last_at ? `Last signed in ${agoText(s.last_at, now)}` : "No sign-in recorded";
  return s.idle ? `${base} · idle (over ${s.idle_days} days)` : base;
}

/** "Secret, valid until 3 Mar 2029 (more than two years)" and the like. */
export function credentialText(c: CredentialRead): string {
  const kind = c.kind === "certificate" ? "Certificate" : "Secret";
  const where = c.via === "registration" ? "on the app registration" : "on the service principal";
  if (!c.end_at) return `${kind} ${where}, no end date`;
  const when = formatDay(c.end_at);
  if (c.expired) return `${kind} ${where}, expired ${when}`;
  return `${kind} ${where}, valid until ${when}${c.long_lived ? " (more than two years)" : ""}`;
}

export function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}
