import type { ReviewDecisionKind, ReviewItem, ReviewRound, SubjectDetail, SubjectKind } from "@/api/client";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-11" → "November 2026". */
export function periodLabel(period: string): string {
  const [y, m] = period.split("-").map(Number);
  return `${MONTHS[m - 1] ?? period} ${y}`;
}

/** The place a round covers, in words. */
export function roundPlace(r: Pick<ReviewRound, "scope_id" | "scope_name">): string {
  if (!r.scope_id) return "everything nobody is responsible for";
  return r.scope_name ?? r.scope_id.split("/").pop() ?? r.scope_id;
}

/** "November review of prod" · "November review for the admins". */
export function roundTitle(r: Pick<ReviewRound, "scope_id" | "scope_name" | "period">): string {
  const month = periodLabel(r.period).split(" ")[0];
  return r.scope_id ? `${month} review of ${roundPlace(r)}` : `${month} review for the admins`;
}

export function decided(r: Pick<ReviewRound, "progress">): number {
  const p = r.progress;
  return p.keep + p.remove + (p.change ?? 0) + p.ask;
}

/** Percent widths for the progress bar: kept, to remove, to change, to ask. */
export function bar(r: Pick<ReviewRound, "progress">): { keep: number; remove: number; change: number; ask: number } {
  const total = Math.max(1, r.progress.total - r.progress.gone);
  const pct = (n: number) => Math.round((100 * n) / total);
  return {
    keep: pct(r.progress.keep),
    remove: pct(r.progress.remove),
    change: pct(r.progress.change ?? 0),
    ask: pct(r.progress.ask),
  };
}

export const DECISION_WORD: Record<ReviewDecisionKind, string> = {
  keep: "Keep",
  remove: "Remove",
  change: "Change",
  ask: "Ask",
};

/** Undecided items with advice, by the decision the advice suggests ("Accept all advice"). */
export function adviceSummary(items: ReviewItem[]): Record<ReviewDecisionKind, number> & { total: number } {
  const out = { keep: 0, remove: 0, change: 0, ask: 0, total: 0 };
  for (const i of items) {
    if (i.decision !== null || i.gone_at !== null || !i.suggested) continue;
    out[i.suggested] += 1;
    out.total += 1;
  }
  return out;
}

export type ItemFilter = "open" | "keep" | "remove" | "change" | "ask" | "gone" | "all";

export function matches(i: ReviewItem, f: ItemFilter): boolean {
  if (f === "all") return true;
  if (f === "gone") return i.gone_at !== null && i.decision !== "remove";
  if (i.gone_at !== null && i.decision !== "remove") return false;
  if (f === "open") return i.decision === null;
  return i.decision === f;
}

/** Clicking the chosen decision again clears it. */
export function nextDecision(current: ReviewDecisionKind | null, clicked: ReviewDecisionKind): ReviewDecisionKind | null {
  return current === clicked ? null : clicked;
}

/** Text safe on one ``#`` comment line: names come from Entra ID and Azure, so no line breaks or control characters. */
export function commentText(value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ");
}

/** All "remove" commands, one per line, for the clipboard. */
export function removeCommands(items: ReviewItem[]): string {
  return items
    .filter((i) => i.decision === "remove" && !i.gone_at)
    .map(
      (i) =>
        `# ${commentText(`${i.principal_name ?? i.principal_id} · ${subjectLine(i) ?? `${i.role_name ?? "role"} on ${i.scope_name ?? i.scope}`}`)}\n${i.remove_command}`,
    )
    .join("\n");
}

/** "due in 22 days" · "due today" · "3 days late". */
export function dueText(dueAt: string, now: Date = new Date()): string {
  const ms = new Date(dueAt).getTime() - now.getTime();
  if (ms < 0) {
    const late = Math.ceil(-ms / 86_400_000);
    return `${late} ${late === 1 ? "day" : "days"} late`;
  }
  const days = Math.ceil(ms / 86_400_000);
  if (days === 0) return "due today";
  return `due in ${days} ${days === 1 ? "day" : "days"}`;
}

const VIA_WORD: Record<string, string> = {
  registration: "its app registration",
  service_principal: "its service principal",
  group: "the group",
};

/** What an owner or secret item is about, in a line; ``null`` for a grant. */
export function subjectLine(x: { kind?: SubjectKind; detail?: SubjectDetail | null; role_name: string | null }): string | null {
  const d = x.detail ?? {};
  const object = d.object_name ?? d.object_id ?? "an app";
  if (x.kind === "owner") {
    const vias = (Array.isArray(d.via) ? d.via : d.via ? [d.via] : []).map((v) => VIA_WORD[v] ?? v);
    const what = d.object_kind === "group" ? "group" : "app";
    return `owns the ${what} ${object}${vias.length ? ` (${vias.join(" and ")})` : ""}, which holds ${x.role_name ?? "a role"}`;
  }
  if (x.kind === "secret") {
    const kind = d.kind === "certificate" ? "Certificate" : "Secret";
    const where = d.via === "service_principal" ? "service principal" : "app registration";
    const end = d.end_at ? new Date(d.end_at) : null;
    const when = !end
      ? "no end date"
      : end.getTime() < Date.now()
        ? `expired ${end.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
        : `valid until ${end.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
    return `${kind} on the ${where} of ${object}, ${when}; it holds ${x.role_name ?? "a role"}`;
  }
  return null;
}

/** The decisions an item can take: an owner stays, goes, or is asked about. */
export function decisionsFor(kind: SubjectKind | undefined): ReviewDecisionKind[] {
  return kind === "owner" ? ["keep", "remove", "ask"] : ["keep", "remove", "change", "ask"];
}

export const KIND_SECTIONS: { kind: SubjectKind; title: string }[] = [
  { kind: "grant", title: "Grants" },
  { kind: "owner", title: "Owners of apps and groups" },
  { kind: "secret", title: "Secrets and certificates" },
];
