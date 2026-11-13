import { describe, expect, it } from "vitest";

import { dueWords } from "./todoUtils";

describe("dueWords", () => {
  const now = new Date("2026-11-20T12:00:00Z");
  it("counts days until due, or overdue", () => {
    expect(dueWords({ status: "open", due_at: "2026-11-25T12:00:00Z", done_at: null }, now)).toBe("due in 5 days");
    expect(dueWords({ status: "open", due_at: "2026-11-21T10:00:00Z", done_at: null }, now)).toBe("due in 1 day");
    expect(dueWords({ status: "open", due_at: "2026-11-17T12:00:00Z", done_at: null }, now)).toBe("3 days overdue");
  });
  it("says done or dismissed", () => {
    expect(dueWords({ status: "dismissed", due_at: "2026-11-17T12:00:00Z", done_at: null }, now)).toBe("dismissed");
    expect(dueWords({ status: "done", due_at: "2026-11-17T12:00:00Z", done_at: null }, now)).toBe("done");
  });
});
