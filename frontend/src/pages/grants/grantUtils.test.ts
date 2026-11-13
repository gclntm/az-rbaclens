import { describe, expect, it } from "vitest";

import { agoText, daysAgo, givenByLabel, grantHref, lastChangeText, originText } from "./grantUtils";

const NOW = new Date("2026-11-08T12:00:00Z");

describe("grant words", () => {
  it("links to the grant page", () => {
    expect(grantHref("ab12")).toBe("/grants/ab12");
  });

  it("names who gave the grant, marking apps", () => {
    expect(givenByLabel({ given_by_name: "sp-terraform", given_by_type: "ServicePrincipal" })).toBe("sp-terraform (app)");
    expect(givenByLabel({ given_by_name: "alice@example.com", given_by_type: "User" })).toBe("alice@example.com");
    expect(givenByLabel({ given_by_id: "dead-beef" })).toBe("an identity RBAC Lens has not named");
    expect(givenByLabel({})).toBeNull();
  });

  it("says since when and by whom, or nothing", () => {
    const since = "2026-11-03T14:12:00Z";
    expect(originText({ since, given_by_name: "sp-terraform", given_by_type: "ServicePrincipal" })).toMatch(
      /^Since .*2026, by sp-terraform \(app\)$/,
    );
    expect(originText({ since })).toMatch(/^Since .*2026$/);
    expect(originText({ given_by_name: "bob" })).toBe("Given by bob");
    expect(originText({})).toBeNull();
  });

  it("counts days and words them", () => {
    expect(daysAgo("2026-11-08T01:00:00Z", NOW)).toBe(0);
    expect(daysAgo("2026-11-10T01:00:00Z", NOW)).toBe(0);
    expect(agoText("2026-11-07T10:00:00Z", NOW)).toBe("yesterday");
    expect(agoText("2026-11-04T10:00:00Z", NOW)).toBe("4 days ago");
  });

  it("never treats a quiet identity as unused", () => {
    expect(lastChangeText(null, NOW)).toBe("No change recorded (reads are not recorded)");
    expect(lastChangeText("2026-11-08T08:00:00Z", NOW)).toBe("Last change today");
  });
});
