import { describe, expect, it } from "vitest";

import { isPaletteShortcut, paletteItems } from "./paletteItems";

describe("paletteItems", () => {
  it("orders scopes, principals, roles and builds their links", () => {
    const items = paletteItems({
      query: "pro",
      scopes: [{ id: "/subscriptions/s1", name: "prod", kind: "subscription" }],
      principals: [
        { id: "abc", name: "alice@example.com", kind: "User" },
        { id: "def", name: null, kind: "ServicePrincipal" },
      ],
      roles: [{ id: "r1", name: "Reader", kind: "BuiltInRole" }],
    });
    expect(items.map((i) => [i.section, i.label, i.href])).toEqual([
      ["Places", "prod", "/places/subscriptions/s1"],
      ["People & apps", "alice@example.com", "/people/abc"],
      ["People & apps", "def", "/people/def"],
      ["Roles", "Reader", "/roles?role=r1"],
    ]);
    expect(items[0].detail).toBe("Subscription");
    expect(items[1].detail).toBe("Person · abc");
    expect(items[2].detail).toBe("App");
    expect(paletteItems(null)).toEqual([]);
  });
});

describe("isPaletteShortcut", () => {
  const key = (k: string, mods: Partial<{ metaKey: boolean; ctrlKey: boolean; altKey: boolean }> = {}) => ({
    key: k,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    ...mods,
  });
  it("accepts ⌘K / Ctrl+K anywhere and / outside fields", () => {
    expect(isPaletteShortcut(key("k", { metaKey: true }), true)).toBe(true);
    expect(isPaletteShortcut(key("K", { ctrlKey: true }), false)).toBe(true);
    expect(isPaletteShortcut(key("/"), false)).toBe(true);
    expect(isPaletteShortcut(key("/"), true)).toBe(false);
    expect(isPaletteShortcut(key("k"), false)).toBe(false);
    expect(isPaletteShortcut(key("k", { metaKey: true, altKey: true }), false)).toBe(false);
  });
});
