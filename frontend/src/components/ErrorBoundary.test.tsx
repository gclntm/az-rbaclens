import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

function Boom({ crash }: { crash: boolean }) {
  if (crash) throw new Error("cannot read this");
  return <p>page drawn</p>;
}

afterEach(cleanup);

describe("ErrorBoundary", () => {
  it("shows what happened instead of a blank page, and tries again at another address", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <MemoryRouter>
        <ErrorBoundary resetKey="/a">
          <Boom crash />
        </ErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert").textContent).toContain("This page could not be shown");
    expect(screen.getByText("cannot read this")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to Home" }).getAttribute("href")).toBe("/");

    rerender(
      <MemoryRouter>
        <ErrorBoundary resetKey="/b">
          <Boom crash={false} />
        </ErrorBoundary>
      </MemoryRouter>,
    );
    expect(screen.getByText("page drawn")).toBeTruthy();
    quiet.mockRestore();
  });
});
