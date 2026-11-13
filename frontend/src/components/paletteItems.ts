import type { ScopeLevel, SearchResults } from "@/api/client";
import { typeLabel, principalHref } from "@/pages/principals/principalUtils";
import { LEVEL_LABEL, scopeHref, scopeLabel } from "@/pages/scopes/scopeUtils";

export type PaletteSection = "Places" | "People & apps" | "Roles";

export type PaletteItem = {
  key: string;
  section: PaletteSection;
  label: string;
  detail: string;
  href: string;
};

/** Flatten search results into one keyboard-navigable list, section by section. */
export function paletteItems(results: SearchResults | null): PaletteItem[] {
  if (!results) return [];
  return [
    ...results.scopes.map((s) => ({
      key: `scope:${s.id}`,
      section: "Places" as const,
      label: scopeLabel(s.id, s.name),
      detail: LEVEL_LABEL[s.kind as ScopeLevel] ?? s.kind ?? "",
      href: scopeHref(s.id),
    })),
    ...results.principals.map((p) => ({
      key: `principal:${p.id}`,
      section: "People & apps" as const,
      label: p.name ?? p.id,
      detail: p.name ? `${typeLabel(p.kind)} · ${p.id}` : typeLabel(p.kind),
      href: principalHref(p.id),
    })),
    ...results.roles.map((r) => ({
      key: `role:${r.id}`,
      section: "Roles" as const,
      label: r.name ?? r.id,
      detail: r.kind === "CustomRole" ? "Custom role" : "Built-in role",
      href: `/roles?role=${encodeURIComponent(r.id)}`,
    })),
  ];
}

/** ⌘K on macOS, Ctrl+K elsewhere; "/" outside text fields. */
export function isPaletteShortcut(e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey">, inField: boolean): boolean {
  if (e.altKey) return false;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") return true;
  return e.key === "/" && !e.metaKey && !e.ctrlKey && !inField;
}
