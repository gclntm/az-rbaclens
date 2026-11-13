/**
 * Settings → Changelog: parse docs/CHANGELOG.md for display.
 *
 * The file is served as a static asset at /CHANGELOG.md — a copy kept in
 * frontend/public/ by scripts/release.sh, because the frontend image is built
 * from frontend/ only. Sections are the `## ` headings:
 *
 * - `## [X.Y.Z] - YYYY-MM-DD` — a release;
 * - `## YYYY-MM-DD` — notes not released yet;
 * - any other `## ` heading (e.g. "History before versioned releases") — a
 *   section shown under its own title.
 *
 * The markdown renderer covers what the changelog uses: `###` / `####`
 * headings, paragraphs, nested `- ` lists with wrapped lines, **bold**,
 * `code` and links. Only absolute http(s) links become links; relative ones
 * point into the repo, not the app, and stay plain text.
 */

export type Release = {
  key: string;
  /** "0.2.0" for a release, null otherwise. */
  version: string | null;
  date: string | null;
  /** The version, "Unreleased", or the heading text. */
  title: string;
  body: string;
};

const TAGGED = /^## \[(\d+\.\d+\.\d+)\]\s*-\s*(\d{4}-\d{2}-\d{2})\s*$/;
const DATED = /^## (\d{4}-\d{2}-\d{2})\s*$/;

export function parseReleases(text: string): Release[] {
  const releases: Release[] = [];
  let current: Omit<Release, "body" | "key"> & { lines: string[] } | null = null;
  const finish = () => {
    if (!current) return;
    const { lines, ...head } = current;
    releases.push({ ...head, key: `${head.title}|${head.date ?? ""}`, body: trimBlank(lines) });
  };
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("## ")) {
      finish();
      const tagged = TAGGED.exec(line);
      const dated = DATED.exec(line);
      current = tagged
        ? { version: tagged[1], date: tagged[2], title: tagged[1], lines: [] }
        : dated
          ? { version: null, date: dated[1], title: "Unreleased", lines: [] }
          : { version: null, date: null, title: line.slice(3).trim(), lines: [] };
      continue;
    }
    current?.lines.push(line);
  }
  finish();
  return releases;
}

function trimBlank(lines: string[]): string {
  let start = 0;
  let end = lines.length;
  while (start < end && !lines[start].trim()) start++;
  while (end > start && (!lines[end - 1].trim() || lines[end - 1].trim() === "---")) end--;
  return lines.slice(start, end).join("\n");
}

export function matchesRelease(release: Release, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [release.title, release.date ?? "", release.body].some((s) => s.toLowerCase().includes(q));
}

// ── Blocks ──────────────────────────────────────────────────────────────

export type ListItem = { text: string; children: ListItem[] };
export type Block =
  | { kind: "h3" | "h4" | "p"; text: string }
  | { kind: "list"; items: ListItem[] };

const isItem = (line: string) => line.trimStart().startsWith("- ");

export function parseBlocks(body: string): Block[] {
  const lines = body.split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ kind: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (!trimmed) {
      flush();
      i++;
    } else if (trimmed.startsWith("#### ")) {
      flush();
      blocks.push({ kind: "h4", text: trimmed.slice(5).trim() });
      i++;
    } else if (trimmed.startsWith("### ")) {
      flush();
      blocks.push({ kind: "h3", text: trimmed.slice(4).trim() });
      i++;
    } else if (isItem(lines[i])) {
      flush();
      const { items, consumed } = parseList(lines, i);
      blocks.push({ kind: "list", items });
      i += consumed;
    } else {
      paragraph.push(trimmed);
      i++;
    }
  }
  flush();
  return blocks;
}

function parseList(lines: string[], start: number): { items: ListItem[]; consumed: number } {
  const root: ListItem[] = [];
  const stack: { indent: number; items: ListItem[] }[] = [{ indent: 0, items: root }];
  let i = start;
  while (i < lines.length) {
    const raw = lines[i];
    const trimmed = raw.trim();
    if (!trimmed) {
      // A blank line ends the list unless another item follows it.
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j >= lines.length || !isItem(lines[j])) break;
      i = j;
      continue;
    }
    if (!isItem(raw)) {
      // A wrapped line continues the last item at the current depth.
      const items = stack[stack.length - 1].items;
      if (!items.length) break;
      items[items.length - 1].text += ` ${trimmed}`;
      i++;
      continue;
    }
    const indent = raw.length - raw.trimStart().length;
    while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
    const top = stack[stack.length - 1];
    if (indent > top.indent && top.items.length) {
      const parent = top.items[top.items.length - 1];
      stack.push({ indent, items: parent.children });
    }
    stack[stack.length - 1].items.push({ text: trimmed.slice(2).trim(), children: [] });
    i++;
  }
  return { items: root, consumed: i - start };
}

// ── Inline ──────────────────────────────────────────────────────────────

export type Inline =
  | { kind: "text" | "strong" | "code"; text: string }
  | { kind: "link"; text: string; href: string };

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g;
const LINK = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0;
    if (index > last) out.push({ kind: "text", text: text.slice(last, index) });
    const token = match[0];
    if (token.startsWith("**")) {
      out.push({ kind: "strong", text: token.slice(2, -2) });
    } else if (token.startsWith("`")) {
      out.push({ kind: "code", text: token.slice(1, -1) });
    } else {
      const [, label, href] = LINK.exec(token) ?? [token, token, ""];
      out.push(/^https?:\/\//i.test(href) ? { kind: "link", text: label, href } : { kind: "text", text: label });
    }
    last = index + token.length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

/** Split text around case-insensitive matches of `query`, for <mark>. */
export function splitMatches(text: string, query: string): { text: string; hit: boolean }[] {
  const q = query.trim().toLowerCase();
  if (!q) return [{ text, hit: false }];
  const parts: { text: string; hit: boolean }[] = [];
  const lower = text.toLowerCase();
  let i = 0;
  while (i < text.length) {
    const at = lower.indexOf(q, i);
    if (at === -1) {
      parts.push({ text: text.slice(i), hit: false });
      break;
    }
    if (at > i) parts.push({ text: text.slice(i, at), hit: false });
    parts.push({ text: text.slice(at, at + q.length), hit: true });
    i = at + q.length;
  }
  return parts;
}
