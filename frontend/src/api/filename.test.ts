import { describe, expect, it } from "vitest";

import { filenameFrom } from "./filename";

describe("filenameFrom", () => {
  it("reads the plain and the UTF-8 forms", () => {
    expect(filenameFrom('attachment; filename="review-2026-11-3.csv"')).toBe("review-2026-11-3.csv");
    expect(filenameFrom("attachment; filename=todos.csv")).toBe("todos.csv");
    expect(filenameFrom("attachment; filename=\"a.csv\"; filename*=UTF-8''r%C3%A9sum%C3%A9.csv")).toBe("résumé.csv");
  });

  it("never returns a path or nothing usable", () => {
    expect(filenameFrom('attachment; filename="../../etc/passwd"')).toBe("passwd");
    expect(filenameFrom('attachment; filename=".."')).toBeNull();
    expect(filenameFrom("inline")).toBeNull();
    expect(filenameFrom(null)).toBeNull();
  });
});
