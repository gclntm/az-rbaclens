import { describe, expect, it } from "vitest";

import { activityAge, whenText } from "./FreshnessLine";

const NOW = new Date(2026, 10, 8, 17, 0);

describe("freshness words", () => {
  it("says today, yesterday or the date", () => {
    expect(whenText(new Date(2026, 10, 8, 6, 2).toISOString(), NOW)).toMatch(/^today /);
    expect(whenText(new Date(2026, 10, 7, 6, 2).toISOString(), NOW)).toMatch(/^yesterday /);
    expect(whenText(new Date(2026, 10, 9, 6, 0).toISOString(), NOW)).not.toMatch(/^(today|yesterday)/);
  });

  it("ages activity in minutes, hours, then days", () => {
    expect(activityAge(new Date(2026, 10, 8, 16, 20).toISOString(), NOW)).toBe("40 min ago");
    expect(activityAge(new Date(2026, 10, 8, 12, 0).toISOString(), NOW)).toBe("5 h ago");
    expect(activityAge(new Date(2026, 10, 1, 12, 0).toISOString(), NOW)).toBe("7 days ago");
  });
});
