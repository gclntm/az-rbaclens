import { describe, expect, it } from "vitest";

import { versionState } from "./VersionTag";

describe("versionState", () => {
  it("knows when the page is older than what runs", () => {
    expect(versionState("0.15.0", "0.15.0")).toBe("same");
    expect(versionState("0.15.0", "0.16.0")).toBe("newer");
    expect(versionState("0.15.0", null)).toBe("unknown");
  });
});
