import { describe, expect, it } from "vitest";

import { treeGuides } from "./treeGuides";

const draw = (depths: number[]) =>
  treeGuides(depths.map((depth) => ({ depth }))).map(
    ({ guides, elbow }) => guides.map((g) => (g === "pipe" ? "│" : " ")).join("") + (elbow === "tee" ? "├" : elbow === "last" ? "└" : "·"),
  );

describe("treeGuides", () => {
  it("draws elbows and continuing lines", () => {
    // root
    //  ├ a
    //  │ └ a1
    //  └ b
    //    ├ b1
    //    └ b2
    expect(draw([0, 1, 2, 1, 2, 2])).toEqual(["·", "├", "│└", "└", " ├", " └"]);
  });

  it("handles several roots and deep last branches", () => {
    expect(draw([0, 1, 0, 1, 2, 3])).toEqual(["·", "└", "·", "└", " └", "  └"]);
  });
});
