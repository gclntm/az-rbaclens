import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider, useToast } from "./Toast";

function Buttons() {
  const { showToast } = useToast();
  return (
    <>
      <button onClick={() => showToast("Saved", "success")}>ok</button>
      <button onClick={() => showToast("Could not save: refused", "error")}>bad</button>
    </>
  );
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("toasts", () => {
  it("lets a success go by itself, and keeps an error until it is dismissed", () => {
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("ok"));
    fireEvent.click(screen.getByText("bad"));
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.queryByText("Saved")).toBeNull();
    const error = screen.getByRole("alert");
    expect(error.textContent).toContain("Could not save: refused");

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
