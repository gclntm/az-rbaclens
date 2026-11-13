import type { ReviewDecisionKind, ReviewItem, ReviewRound } from "@/api/client";

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
        `# ${commentText(`${i.principal_name ?? i.principal_id} · ${i.role_name ?? "role"} on ${i.scope_name ?? i.scope}`)}\n${i.remove_command}`,
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
