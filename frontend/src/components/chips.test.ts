import { describe, expect, it } from "vitest";

import { decisionClass, pillClass } from "./chips";

const classes = (s: string) => s.split(/\s+/);

describe("decisionClass", () => {
  it("is a plain secondary button until chosen", () => {
    const c = classes(decisionClass("keep", false));
    expect(c).toContain("bg-primary");
    expect(c).toContain("review-keep");
    expect(c).not.toContain("is-on");
  });

  it("fills a chosen decision with its own colour instead of the plain background", () => {
    const c = classes(decisionClass("remove", true));
    expect(c).toContain("bg-(--accent-red)");
    expect(c).toContain("text-(--on-accent)");
    expect(c).not.toContain("bg-primary");
    expect(c).toContain("is-on");
  });

  it("outlines the advice's suggestion", () => {
    expect(classes(decisionClass("ask", false, true))).toContain("is-suggested");
    expect(classes(decisionClass("ask", false, false))).not.toContain("is-suggested");
  });
});

describe("pillClass", () => {
  it("marks the chosen pill", () => {
    expect(classes(pillClass(true))).toContain("is-active");
    expect(classes(pillClass(false))).not.toContain("is-active");
  });
});
