import { describe, expect, it } from "vitest";

import { actionable, controlsText, ruleId } from "./adviceUtils";

describe("advice helpers", () => {
  it("names the ISO controls and the rule id", () => {
    expect(controlsText(["8.2", "5.18"])).toBe("A.8.2 Privileged access rights · A.5.18 Access rights");
    expect(controlsText(["9.9"])).toBe("A.9.9");
    expect(ruleId("R02 standing-admin")).toBe("R02");
  });

  it("treats only info as nothing to do", () => {
    expect(actionable("must")).toBe(true);
    expect(actionable("could")).toBe(true);
    expect(actionable("info")).toBe(false);
  });
});
