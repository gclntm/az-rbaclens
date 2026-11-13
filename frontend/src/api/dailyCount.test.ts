import { describe, expect, it } from "vitest";

import { dailyCountText } from "./client";

describe("dailyCountText", () => {
  it("says nothing for an event kept in full", () => {
    expect(dailyCountText({ event_count: 1, first_occurred_at: null })).toBe("");
    expect(dailyCountText({})).toBe("");
  });
  it("gives the count and the first time of a daily row", () => {
    const text = dailyCountText({ event_count: 1234, first_occurred_at: "2026-11-01T08:05:00Z" });
    expect(text.startsWith(`×${(1234).toLocaleString()} from `)).toBe(true);
  });
});
