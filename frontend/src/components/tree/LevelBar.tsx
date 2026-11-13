import type { CapabilityLevel } from "@/api/client";

const ORDER: CapabilityLevel[] = ["access", "keys", "change", "data", "view", "unknown"];
/** Each level's colour in the bar. */
const LB: Record<CapabilityLevel, string> = {
  access: "bg-(--accent-red)",
  keys: "bg-(--accent-orange)",
  change: "bg-(--accent-blue)",
  data: "bg-(--accent-purple)",
  view: "bg-(--text-muted)",
  unknown: "bg-(--accent-pink)",
};
const WORDS: Record<CapabilityLevel, string> = {
  access: "can change access",
  keys: "can get keys",
  change: "can change resources",
  data: "work with data",
  view: "can only look",
  unknown: "unknown role",
};

/** "3 can change access · 5 can get keys · …" */
export function levelSummary(levels: Partial<Record<string, number>>): string {
  return ORDER.filter((l) => (levels[l] ?? 0) > 0)
    .map((l) => `${levels[l]} ${WORDS[l]}`)
    .join(" · ");
}

/** A small stacked bar of grants by capability level, strongest first. */
export default function LevelBar({ levels }: { levels: Partial<Record<string, number>> }) {
  const total = ORDER.reduce((n, l) => n + (levels[l] ?? 0), 0);
  if (total === 0) return <span className="text-[0.75rem] text-quaternary">—</span>;
  const summary = levelSummary(levels);
  return (
    <span className="flex h-2 w-full max-w-[140px] gap-px overflow-hidden rounded-full bg-tertiary" title={`${total} grants: ${summary}`} role="img" aria-label={`${total} grants: ${summary}`}>
      {ORDER.map((l) =>
        levels[l] ? <span key={l} className={`block h-full min-w-[3px] ${LB[l]}`} style={{ flexGrow: levels[l] }} /> : null,
      )}
    </span>
  );
}
