import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type SearchResults } from "@/api/client";

import { isPaletteShortcut, paletteItems } from "./paletteItems";

const DEBOUNCE_MS = 150;
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const PALETTE_HINT = IS_MAC ? "⌘K" : "Ctrl K";

function inTextField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

/**
 * Global search over scopes, principals and roles. Opens with ⌘K / Ctrl+K
 * (or "/" outside a text field) and from the sidebar button; arrow keys
 * move, Enter opens, Esc closes.
 */
export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const items = useMemo(() => paletteItems(results), [results]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isPaletteShortcut(e, inTextField(e.target))) {
        e.preventDefault();
        returnFocus.current = document.activeElement as HTMLElement | null;
        setOpen(true);
      }
    }
    function onOpenRequest() {
      returnFocus.current = document.activeElement as HTMLElement | null;
      setOpen(true);
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-command-palette", onOpenRequest);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("open-command-palette", onOpenRequest);
    };
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    document.getElementById(`palette-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 2) {
      setResults(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    const t = setTimeout(() => {
      api
        .search(q, ctrl.signal)
        .then((r) => {
          setResults(r);
          setActive(0);
        })
        .catch(() => {
          if (!ctrl.signal.aborted) setResults(null);
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, open]);

  function close() {
    setOpen(false);
    setQuery("");
    setResults(null);
    returnFocus.current?.focus?.();
  }

  function go(index: number) {
    const item = items[index];
    if (!item) return;
    close();
    navigate(item.href);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (items.length ? (i + 1) % items.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (items.length ? (i - 1 + items.length) % items.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(active);
    }
  }

  if (!open) return null;

  const q = query.trim();
  let lastSection = "";
  return (
    // A click beside the palette closes it; Escape does from the keyboard.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="uui fixed inset-0 z-60 flex items-start justify-center bg-overlay/70 px-4 pt-[12vh] pb-4 backdrop-blur-[6px]"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      {/* The keys (arrows, Enter, Escape) belong to the whole dialog: focus stays in its input. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        className="flex max-h-[70vh] w-full max-w-160 flex-col overflow-hidden rounded-xl bg-primary shadow-xl ring-1 ring-secondary"
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        onKeyDown={onKeyDown}
      >
        <input
          ref={inputRef}
          className="border-b border-secondary bg-transparent px-4 py-3.5 text-md text-primary outline-hidden placeholder:text-placeholder"
          type="text"
          role="combobox"
          aria-expanded={items.length > 0}
          aria-controls="palette-results"
          aria-activedescendant={items[active] ? `palette-${active}` : undefined}
          aria-autocomplete="list"
          placeholder="Search places, people, apps, roles…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ul id="palette-results" className="overflow-y-auto py-1" role="listbox" aria-label="Results">
          {items.map((item, i) => {
            const header = item.section !== lastSection;
            lastSection = item.section;
            return (
              <li key={item.key} role="presentation">
                {header && <div className="px-4 pt-2 pb-1 text-xs font-semibold tracking-wide text-quaternary uppercase" aria-hidden="true">{item.section}</div>}
                {/* Combobox pattern: the input keeps the focus and points at the active option
                    (aria-activedescendant); arrows and Enter work there, a click works here. */}
                {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus */}
                <div
                  id={`palette-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={`flex min-h-11 cursor-pointer flex-col justify-center gap-0.5 px-4 py-2 ${i === active ? "bg-[var(--chip-active-bg)]" : ""}`}
                  onMouseMove={() => setActive(i)}
                  onClick={() => go(i)}
                >
                  <span className="text-sm text-primary [overflow-wrap:anywhere]">{item.label}</span>
                  <span className="text-xs text-tertiary [overflow-wrap:anywhere]">{item.detail}</span>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="border-t border-secondary px-4 py-2 text-xs text-quaternary" aria-live="polite">
          {q.length < 2
            ? "Type at least 2 characters"
            : loading
              ? "Searching…"
              : items.length === 0
                ? `Nothing matches “${q}”`
                : "↑↓ to move · Enter to open · Esc to close"}
        </div>
      </div>
    </div>
  );
}
