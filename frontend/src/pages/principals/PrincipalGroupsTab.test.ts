import { describe, expect, it } from "vitest";

import { emptyGroupText } from "./PrincipalGroupsTab";

describe("emptyGroupText", () => {
  it("blames a permission only when the members could not be read", () => {
    expect(emptyGroupText(true)).toMatch(/^This group has no members in Entra ID/);
    expect(emptyGroupText(false)).toMatch(/could not be read from Microsoft Graph/);
    expect(emptyGroupText(undefined)).toMatch(/could not be read/);
  });
});
