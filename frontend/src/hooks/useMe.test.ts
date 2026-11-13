import { describe, expect, it } from "vitest";

import { placesText } from "./useMe";

describe("placesText", () => {
  it("lists places in words", () => {
    expect(placesText([])).toBe("");
    expect(placesText(["prod"])).toBe("prod");
    expect(placesText(["prod", "data-lake"])).toBe("prod and data-lake");
    expect(placesText(["a", "b", "c"])).toBe("a, b and c");
    expect(placesText(["a", "b", "c", "d"])).toBe("a, b and 2 more");
  });
});
