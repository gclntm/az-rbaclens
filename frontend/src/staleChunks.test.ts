import { describe, expect, it } from "vitest";

import { shouldReload } from "./staleChunks";

function memory(): Pick<Storage, "getItem" | "setItem"> {
  const m = new Map<string, string>();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
}

describe("shouldReload", () => {
  it("reloads once, then not again within 10 seconds", () => {
    const s = memory();
    expect(shouldReload(1_000_000, s)).toBe(true);
    expect(shouldReload(1_005_000, s)).toBe(false);
    expect(shouldReload(1_011_000, s)).toBe(true);
  });
});
