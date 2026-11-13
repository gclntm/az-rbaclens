import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Me } from "@/api/client";

let reviews: () => Promise<unknown[]>;
vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: { reviews: { list: () => reviews() } },
}));
vi.mock("@/pages/changes/ChangesFeed", () => ({ default: () => null }));

import MyHome from "./MyHome";

const me: Me = { signed_in: true, oid: "oid-1", name: "Ana", is_admin: false, responsible_for: [] };

afterEach(cleanup);

describe("reviewer Home", () => {
  it("says the reviews could not be loaded, not that none is waiting", async () => {
    reviews = () => Promise.reject(new Error("502 Bad Gateway"));
    render(
      <MemoryRouter>
        <MyHome me={me} />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Could not load your reviews/)).toBeTruthy();
    expect(screen.queryByText("No review waiting for you.")).toBeNull();
  });

  it("says none is waiting when the list is empty", async () => {
    reviews = () => Promise.resolve([]);
    render(
      <MemoryRouter>
        <MyHome me={me} />
      </MemoryRouter>,
    );
    expect(await screen.findByText("No review waiting for you.")).toBeTruthy();
  });
});
