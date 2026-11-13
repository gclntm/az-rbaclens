import { describe, expect, it } from "vitest";

import { matchesRelease, parseBlocks, parseInline, parseReleases, splitMatches } from "./changelog";

const SAMPLE = `# Changelog

Intro text that is not a release.

## 2026-11-09

- Not released yet.

## [0.2.1] - 2026-11-08

### Fixed
- **Lint.** Ruff findings fixed in
  \`app/api/health.py\`.
  - nested detail

---

## History before versioned releases

- **2026-10-23** — older entry.
`;

describe("parseReleases", () => {
  it("splits on ## headings: unreleased, releases and other sections", () => {
    const rs = parseReleases(SAMPLE);
    expect(rs.map((r) => [r.title, r.version, r.date])).toEqual([
      ["Unreleased", null, "2026-11-09"],
      ["0.2.1", "0.2.1", "2026-11-08"],
      ["History before versioned releases", null, null],
    ]);
    expect(rs[1].body.startsWith("### Fixed")).toBe(true);
    expect(rs[1].body.endsWith("nested detail")).toBe(true); // trailing --- dropped
    expect(new Set(rs.map((r) => r.key)).size).toBe(3);
  });

  it("ignores the preamble and handles an empty file", () => {
    expect(parseReleases("# Changelog\n\nNothing yet.\n")).toEqual([]);
  });
});

describe("parseBlocks", () => {
  it("joins wrapped list lines and nests indented items", () => {
    const blocks = parseBlocks(parseReleases(SAMPLE)[1].body);
    expect(blocks[0]).toEqual({ kind: "h3", text: "Fixed" });
    expect(blocks[1]).toEqual({
      kind: "list",
      items: [
        {
          text: "**Lint.** Ruff findings fixed in `app/api/health.py`.",
          children: [{ text: "nested detail", children: [] }],
        },
      ],
    });
  });

  it("keeps paragraphs and a list split by a blank line together", () => {
    expect(parseBlocks("First line\nsecond line\n\n- a\n\n- b")).toEqual([
      { kind: "p", text: "First line second line" },
      { kind: "list", items: [{ text: "a", children: [] }, { text: "b", children: [] }] },
    ]);
  });
});

describe("parseInline", () => {
  it("reads bold, code and links; relative links stay text", () => {
    expect(parseInline("**Bold** and `code` see [docs](https://example.com) or [file](README-MAKE.md).")).toEqual([
      { kind: "strong", text: "Bold" },
      { kind: "text", text: " and " },
      { kind: "code", text: "code" },
      { kind: "text", text: " see " },
      { kind: "link", text: "docs", href: "https://example.com" },
      { kind: "text", text: " or " },
      { kind: "text", text: "file" },
      { kind: "text", text: "." },
    ]);
  });

  it("never turns a javascript: URL into a link", () => {
    expect(parseInline("[x](javascript:alert(1))").some((p) => p.kind === "link")).toBe(false);
  });
});

describe("search", () => {
  it("matches title, date or notes, case-insensitively", () => {
    const [unreleased, release] = parseReleases(SAMPLE);
    expect(matchesRelease(release, "RUFF")).toBe(true);
    expect(matchesRelease(release, "2026-11-08")).toBe(true);
    expect(matchesRelease(unreleased, "ruff")).toBe(false);
    expect(matchesRelease(unreleased, "  ")).toBe(true);
  });

  it("splits text around matches", () => {
    expect(splitMatches("Ruff and ruff", "RUFF")).toEqual([
      { text: "Ruff", hit: true },
      { text: " and ", hit: false },
      { text: "ruff", hit: true },
    ]);
    expect(splitMatches("abc", "")).toEqual([{ text: "abc", hit: false }]);
  });
});
