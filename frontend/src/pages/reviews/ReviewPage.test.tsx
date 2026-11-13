import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReviewDecisionKind, ReviewItem, ReviewRoundDetail } from "@/api/client";
import { ToastProvider } from "@/components/Toast";

let round: ReviewRoundDetail;
const decide = vi.fn(async (_rid: number, itemId: number, decision: ReviewDecisionKind | null, note?: string | null) => {
  round = {
    ...round,
    items: round.items.map((i) => (i.id === itemId ? { ...i, decision, note: note ?? null } : i)),
  };
});

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: {
    reviews: {
      detail: async () => round,
      decide: (rid: number, itemId: number, d: ReviewDecisionKind | null, note?: string | null) => decide(rid, itemId, d, note),
      exportUrl: () => "/api/reviews/1/export",
    },
  },
}));

import ReviewPage from "./ReviewPage";

function item(over: Partial<ReviewItem>): ReviewItem {
  return {
    id: 1, grant_id: "g1", scope: "/subscriptions/s1", scope_name: "prod", scope_level: "subscription",
    principal_id: "alice-id", principal_name: "Alice", principal_type: "User",
    role_definition_id: "/providers/Microsoft.Authorization/roleDefinitions/r1", role_name: "Contributor",
    level: "change", moved_from_scope: null, moved_from_name: null, decision: null, note: null,
    decided_by_name: null, decided_at: null, carried: false, ask_remind_at: null, gone_at: null,
    remove_command: "az role assignment delete …", ...over,
  };
}

function show(canDecide = true) {
  round = {
    id: 1, scope_id: "/subscriptions/s1", scope_name: "prod", scope_level: "subscription", period: "2026-11",
    reviewer_id: "me", reviewer_name: "Me", opened_at: "2026-11-01T06:00:00Z", due_at: "2026-11-30T06:00:00Z",
    finished_at: null, finished_by_name: null, blocking: 2, can_decide: canDecide,
    progress: { total: 2, keep: 0, remove: 0, change: 0, ask: 0, open: 2, gone: 0 },
    items: [item({ id: 1 }), item({ id: 2, principal_id: "bob-id", principal_name: "Bob" })],
  };
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/reviews/1"]}>
        <Routes>
          <Route path="/reviews/:id" element={<ReviewPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

const card = (name: string) => screen.getByText(name).closest("article") as HTMLElement;
const button = (name: string, label: string) =>
  [...card(name).querySelectorAll("button")].find((b) => b.textContent === label) as HTMLButtonElement;

beforeEach(() => decide.mockClear());
afterEach(cleanup);

describe("review decisions", () => {
  it("keeps a grant in one click, and a second click on it takes the decision back", async () => {
    show();
    await screen.findByText("Alice");
    fireEvent.click(screen.getByRole("button", { name: "All 2" }));
    fireEvent.click(button("Alice", "Keep"));
    await waitFor(() => expect(decide).toHaveBeenCalledWith(1, 1, "keep", undefined));
    await waitFor(() => expect(button("Alice", "Keep").getAttribute("aria-pressed")).toBe("true"));

    fireEvent.click(button("Alice", "Keep"));
    await waitFor(() => expect(decide).toHaveBeenLastCalledWith(1, 1, null, undefined));
  });

  it("asks what to change in place before deciding Change", async () => {
    show();
    await screen.findByText("Bob");
    fireEvent.click(button("Bob", "Change"));
    expect(decide).not.toHaveBeenCalled();
    const note = screen.getByLabelText("What to change");
    fireEvent.change(note, { target: { value: "Make it PIM-eligible" } });
    fireEvent.submit(note.closest("form")!);
    await waitFor(() => expect(decide).toHaveBeenCalledWith(1, 2, "change", "Make it PIM-eligible"));
  });

  it("shows no decision buttons to someone who cannot decide", async () => {
    show(false);
    await screen.findByText("Alice");
    expect(button("Alice", "Keep")).toBeUndefined();
    expect(screen.getByText("Only the reviewer or an admin can decide here.")).toBeTruthy();
  });
});
