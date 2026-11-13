import { describe, expect, it } from "vitest";

import { compareNumbers, cssString, hoursText, statusTrend } from "./reportUtils";

describe("report helpers", () => {
  it("compares key numbers with a saved assessment; lower is better", () => {
    const rows = compareNumbers({ orphans: 2, least_privilege: 12, todos_overdue: 0 }, { orphans: 9, least_privilege: 10 });
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.orphans).toMatchObject({ before: 9, now: 2, better: true });
    expect(by.least_privilege).toMatchObject({ before: 10, now: 12, better: false });
    expect(by.todos_overdue).toMatchObject({ before: null, now: 0, better: null });
  });

  it("says whether a control got better", () => {
    expect(statusTrend("met", "partly")).toBe("better");
    expect(statusTrend("not_met", "partly")).toBe("worse");
    expect(statusTrend("met", "met")).toBe("same");
    expect(statusTrend("met", undefined)).toBeNull();
  });

  it("writes hours and days", () => {
    expect(hoursText(5.4)).toBe("5 h");
    expect(hoursText(96)).toBe("4 days");
    expect(hoursText(null)).toBe("—");
  });

  it("makes a safe CSS string for the printed footer", () => {
    expect(cssString('prod "main"')).toBe('"prod \\"main\\""');
    expect(cssString("a</style>b")).toBe('"a /style b"');
  });
});
