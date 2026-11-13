import { useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "@/api/client";
import { BADGE, BADGE_TONE, CAPTION, cx } from "@/components/classes";
import { SectionCard } from "@/components/PageTemplate";

import {
  matchesRelease,
  parseBlocks,
  parseInline,
  parseReleases,
  splitMatches,
  type Block,
  type ListItem,
  type Release,
} from "./changelog";

/** Releases on the left, the chosen one's notes on the right; one column on a narrow screen. */
const CHANGELOG =
  "mt-4 grid h-[calc(100vh_-_300px)] min-h-[420px] grid-cols-[240px_minmax(0,1fr)] gap-4 max-[860px]:h-auto max-[860px]:grid-cols-[1fr] " +
  "[&_mark]:rounded-[2px] [&_mark]:bg-(--accent-orange-subtle) [&_mark]:text-inherit";
const ENTRY =
  "flex min-h-11 w-full cursor-pointer flex-col items-start gap-0.5 rounded-[4px] border px-3 py-2 text-left text-[0.8125rem] text-primary " +
  "[font-family:inherit] [font-style:inherit] [font-weight:inherit] [line-height:inherit]";
const ENTRY_STATE = {
  active: "border-(--chip-active-border) bg-(--chip-active-bg)",
  idle: "border-transparent bg-transparent hover:bg-primary_hover",
} as const;
/** The release notes, from markdown: bare headings, lists, code and links styled from here. */
const NOTES =
  "min-h-0 overflow-y-auto rounded-[8px] border border-secondary px-6 py-4 text-[0.875rem] leading-[1.6] " +
  "[&_h4]:mx-0 [&_h4]:mt-4 [&_h4]:mb-1 [&_h4]:text-[0.875rem] [&_h4]:font-bold [&_h4]:text-(--accent-blue) " +
  "[&_h5]:mx-0 [&_h5]:mt-3 [&_h5]:mb-1 [&_h5]:text-[0.8125rem] [&_h5]:font-bold " +
  "[&_p]:mx-0 [&_p]:my-1.5 " +
  "[&_ul]:mx-0 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-[22px] [&_ul_ul]:list-[circle] [&_ul_ul_ul]:list-[square] " +
  "[&_li]:mx-0 [&_li]:my-1.5 " +
  "[&_code]:rounded-[2px] [&_code]:border [&_code]:border-secondary [&_code]:bg-primary_hover [&_code]:px-[3px] [&_code]:py-0 [&_code]:font-mono [&_code]:text-[0.78em] " +
  "[&_a]:text-(--accent-blue)";

export default function ChangelogTab() {
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch("/CHANGELOG.md", { cache: "no-cache" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setReleases(parseReleases(text));
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    api
      .health()
      .then((h) => {
        if (!cancelled) setRunning(h.version ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const shown = useMemo(() => (releases ?? []).filter((r) => matchesRelease(r, query)), [releases, query]);
  const selected = shown.find((r) => r.key === selectedKey) ?? shown[0];

  return (
    <SectionCard
      icon="history"
      title="Changelog"
      actions={
        <>
          {running && <span className={CAPTION}>Running version {running}</span>}
        </>
      }
    >
      <p className="my-[1em] text-quaternary">What changed in each release, newest first.</p>

      {error && <p className="my-[1em] text-error-primary">Could not load the changelog: {error}</p>}
      {!error && releases === null && <p className="my-[1em] text-quaternary">Loading…</p>}
      {releases?.length === 0 && <p className="my-[1em] text-quaternary">The changelog is empty.</p>}

      {releases && releases.length > 0 && (
        <div className={CHANGELOG}>
          <nav className="flex min-h-0 flex-col overflow-hidden rounded-[8px] border border-secondary max-[860px]:max-h-[260px]" aria-label="Releases">
            <input
              type="search"
              className="font-normal tracking-normal m-2 w-auto"
              placeholder="Search notes…"
              aria-label="Search the changelog"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <ul className="m-0 flex-1 list-none overflow-y-auto px-1 pt-0 pb-1">
              {shown.length === 0 && <li className="p-2 text-[0.8125rem] text-quaternary">No matches</li>}
              {shown.map((r) => (
                <li key={r.key}>
                  <button
                    type="button"
                    className={cx("tracking-normal", ENTRY, r === selected ? ENTRY_STATE.active : ENTRY_STATE.idle)}
                    aria-current={r === selected ? "true" : undefined}
                    onClick={() => setSelectedKey(r.key)}
                  >
                    <span className="inline-flex items-center gap-2 font-semibold">
                      {r.title}
                      {r.version && r.version === running && <span className={cx(BADGE, BADGE_TONE.done)}>running</span>}
                    </span>
                    {r.date && <span className={CAPTION}>{r.date}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          {selected && (
            <article className={NOTES}>
              <header className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-2 border-b border-b-secondary pb-2">
                <h3 className="m-0 text-[1rem] font-bold">{selected.version ? `Version ${selected.version}` : selected.title}</h3>
                {selected.date && <span className={CAPTION}>{selected.date}</span>}
                {selected.version && selected.version === running && (
                  <span className={cx(BADGE, BADGE_TONE.done)}>running</span>
                )}
                {!selected.version && selected.date && <span className={cx(BADGE, BADGE_TONE.pending)}>not released yet</span>}
              </header>
              {parseBlocks(selected.body).map((b, i) => (
                <BlockView key={i} block={b} query={query} />
              ))}
            </article>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function BlockView({ block, query }: { block: Block; query: string }) {
  if (block.kind === "list") return <Items items={block.items} query={query} />;
  if (block.kind === "h3") return <h4>{inline(block.text, query)}</h4>;
  if (block.kind === "h4") return <h5>{inline(block.text, query)}</h5>;
  return <p className="my-[1em]">{inline(block.text, query)}</p>;
}

function Items({ items, query }: { items: ListItem[]; query: string }) {
  return (
    <ul>
      {items.map((it, i) => (
        <li key={i}>
          {inline(it.text, query)}
          {it.children.length > 0 && <Items items={it.children} query={query} />}
        </li>
      ))}
    </ul>
  );
}

function inline(text: string, query: string): ReactNode[] {
  return parseInline(text).map((part, i) => {
    const marked = highlight(part.text, query);
    if (part.kind === "code") return <code key={i}>{marked}</code>;
    if (part.kind === "strong") return <strong key={i}>{marked}</strong>;
    if (part.kind === "link")
      return (
        <a key={i} href={part.href} target="_blank" rel="noopener noreferrer">
          {marked}
        </a>
      );
    return <span key={i}>{marked}</span>;
  });
}

function highlight(text: string, query: string): ReactNode[] {
  return splitMatches(text, query).map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : p.text));
}
