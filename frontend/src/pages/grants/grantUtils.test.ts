import { describe, expect, it } from "vitest";

import {
  agoText,
  credentialText,
  daysAgo,
  givenByLabel,
  grantHref,
  lastChangeText,
  originText,
  signInText,
} from "./grantUtils";

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

describe("signInText and credentialText", () => {
  it("say when someone last signed in, and whether that is idle", () => {
    expect(signInText({ last_at: "2026-11-06T09:00:00Z", read_at: null, idle: false, idle_days: 90 }, NOW)).toBe(
      "Last signed in 2 days ago",
    );
    expect(signInText({ last_at: null, read_at: null, idle: true, idle_days: 90 }, NOW)).toBe(
      "No sign-in recorded · idle (over 90 days)",
    );
  });
  it("describe a secret by where it is and when it ends", () => {
    const base = { start_at: null, expired: false, long_lived: false } as const;
    expect(credentialText({ ...base, via: "registration", kind: "secret", end_at: null })).toBe(
      "Secret on the app registration, no end date",
    );
    expect(
      credentialText({ ...base, via: "service_principal", kind: "certificate", end_at: "2026-01-01T00:00:00Z", expired: true }),
    ).toMatch(/^Certificate on the service principal, expired /);
    expect(
      credentialText({ ...base, via: "registration", kind: "secret", end_at: "2029-03-03T00:00:00Z", long_lived: true }),
    ).toMatch(/\(more than two years\)$/);
  });
});

