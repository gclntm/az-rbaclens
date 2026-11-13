import { describe, expect, it } from "vitest";

import { notificationText } from "./NotificationsTab";

describe("notification text", () => {
  it("words each kind", () => {
    expect(notificationText({ kind: "ask", payload: { to_the_person: true, role: "Owner", place: "prod", note: "Still?" } })).toBe(
      'Do you still need Owner on prod? "Still?"',
    );
    expect(notificationText({ kind: "ask", payload: { to_the_person: false, who: "grp-1", role: "Reader", place: "rg" } })).toBe(
      "Question about grp-1 (Reader on rg)",
    );
    expect(notificationText({ kind: "review_opened", payload: { place: "prod", period: "2026-11" } })).toBe(
      "Your review of prod for 2026-11 is open.",
    );
    expect(notificationText({ kind: "review_opened", payload: { place: null, period: "2026-11" } })).toBe(
      "The admins' review for 2026-11 is open.",
    );
    expect(notificationText({ kind: "reminder", payload: { who: "Tom", role: "Owner", place: "prod" } })).toBe(
      "Still open: the question about Tom (Owner on prod).",
    );
  });
});
