import { describe, expect, it } from "vitest";

import { countText, isCurrent } from "./navUtils";

describe("isCurrent", () => {
  it("matches Home only on the exact path", () => {
    expect(isCurrent("/", "/", true)).toBe(true);
    expect(isCurrent("/places", "/", true)).toBe(false);
  });

  it("matches an item's path and the pages below it", () => {
    expect(isCurrent("/places", "/places")).toBe(true);
    expect(isCurrent("/places/subscriptions/abc", "/places")).toBe(true);
    expect(isCurrent("/reviews/12", "/reviews")).toBe(true);
    expect(isCurrent("/settings/changelog", "/settings")).toBe(true);
    expect(isCurrent("/places/", "/places")).toBe(true);
  });

  it("does not match a path that only starts with the same letters", () => {
    expect(isCurrent("/peoplex", "/people")).toBe(false);
    expect(isCurrent("/admin/database", "/admin/data")).toBe(false);
    expect(isCurrent("/grants/1", "/places")).toBe(false);
  });
});

describe("countText", () => {
  it("caps at 999+", () => {
    expect(countText(7)).toBe("7");
    expect(countText(999)).toBe("999");
    expect(countText(1000)).toBe("999+");
  });
});
