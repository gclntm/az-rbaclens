import type { AccessChange, ChangeKind } from "@/api/client";

export const GIVEN: ChangeKind[] = ["granted", "eligible_added", "member_added"];

export function isGiven(kind: ChangeKind): boolean {
  return GIVEN.includes(kind);
}

/** The pieces of one change sentence, so the page can link names. */
export type ChangeParts = {
  verb: string;
  /** Role phrase after the verb, e.g. "Owner". */
  role: string;
  /** For memberships: the group, linked by the page. */
  group: string | null;
  /** Text between group and role, e.g. ", so they now have". */
  bridge: string | null;
};

/**
 * "{who} was given {Owner} on {prod}" · "{who} lost …" · "{who} can now switch on …" ·
 * "{who} joined {grp}, so they now have {Reader} on {rg}".
 */
export function changeParts(c: AccessChange): ChangeParts {
  const role = c.role_name ?? "a role";
  const group = c.via_group_name ?? c.via_group_id;
  switch (c.kind) {
    case "granted":
      return { verb: "was given", role, group: null, bridge: null };
    case "removed":
      return { verb: "lost", role, group: null, bridge: null };
    case "eligible_added":
      return { verb: "can now switch on", role, group: null, bridge: null };
    case "eligible_removed":
      return { verb: "can no longer switch on", role, group: null, bridge: null };
    case "member_added":
      return { verb: "joined", role, group, bridge: ", so they now have" };
    case "member_removed":
      return { verb: "left", role, group, bridge: ", and lost" };
  }
}

/** Plain text of the whole sentence (for titles and tests). */
export function changeSentence(c: AccessChange): string {
  const who = c.principal_name ?? c.principal_id;
  const where = c.scope_name ?? c.scope.split("/").pop() ?? c.scope;
  const p = changeParts(c);
  const pim = c.kind === "eligible_added" ? " on request (PIM)" : "";
  return p.group
    ? `${who} ${p.verb} ${p.group}${p.bridge} ${p.role} on ${where}`
    : `${who} ${p.verb} ${p.role} on ${where}${pim}`;
}

/** Where the time and author come from, in words. */
export function sourceText(c: AccessChange): string {
  const by = c.actor_name ?? (c.actor_id ? "an identity RBAC Lens has not named" : null);
  if (c.source === "activity_log") return by ? `by ${by} · from the Azure Activity Log` : "from the Azure Activity Log";
  if (c.source === "created_on") return by ? `by ${by} · as recorded by Azure` : "as recorded by Azure";
  if (c.source === "entra_audit") {
    // An app is how Entra records a change made through a service: Outlook or Teams for
    // Microsoft 365 groups, access packages, PIM, the sync from on-premises AD.
    const who = by ? (c.actor_type === "app" ? `by the app ${by}` : `by ${by}`) : null;
    const where = c.direct_group_name ? `in ${c.direct_group_name}, inside ${c.via_group_name ?? "the group"}` : null;
    return [who, where, "from the Entra ID audit log"].filter(Boolean).join(" · ");
  }
  if (c.kind === "member_added" || c.kind === "member_removed") {
    return "first seen by the daily check · who changed the group is not known here: see the Entra ID audit log";
  }
  return "first seen by the daily check · the Activity Log may still say who did it";
}

/** "14:12", or "~06:02" when the time is only when a daily check first saw it. */
export function timeText(c: AccessChange): string {
  const t = new Date(c.occurred_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return c.source === "daily_check" ? `~${t}` : t;
}

/** Local calendar day key, e.g. "2026-11-03". */
export function dayKey(iso: string): string {
  const d = new Date(iso);
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

/** Changes grouped by local day, newest day first (input is newest first). */
export function byDay(items: AccessChange[]): { key: string; label: string; items: AccessChange[] }[] {
  const out: { key: string; label: string; items: AccessChange[] }[] = [];
  for (const c of items) {
    const key = dayKey(c.occurred_at);
    const last = out[out.length - 1];
    if (last && last.key === key) last.items.push(c);
    else out.push({ key, label: dayLabel(c.occurred_at), items: [c] });
  }
  return out;
}
