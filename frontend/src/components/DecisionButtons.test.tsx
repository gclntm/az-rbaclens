import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DecisionButtons from "./DecisionButtons";

afterEach(cleanup);

describe("DecisionButtons", () => {
  it("asks the reason for accepting a risk in place, and needs one", () => {
    const onDecide = vi.fn();
    render(<DecisionButtons open busy={false} what="2 reasons" onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept risk…" }));

    const reason = screen.getByLabelText(/Why is this risk acceptable \(2 reasons\)/);
    expect(document.activeElement).toBe(reason);
    const save = screen.getByRole("button", { name: "Accept risk" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(reason, { target: { value: "  break-glass account  " } });
    fireEvent.click(save);
    expect(onDecide).toHaveBeenCalledWith({ decision: "accepted_risk", note: "break-glass account" }, "Risk accepted: 2 reasons");
    expect(screen.getByRole("button", { name: "Mark reviewed" })).toBeTruthy();
  });

  it("lets Escape or Cancel step back without deciding", () => {
    const onDecide = vi.fn();
    render(<DecisionButtons open busy={false} onDecide={onDecide} />);
    fireEvent.click(screen.getByRole("button", { name: "Accept risk…" }));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(onDecide).not.toHaveBeenCalled();
  });

  it("decides in one click otherwise, and offers Reopen once decided", () => {
    const onDecide = vi.fn();
    const onReopen = vi.fn();
    const { rerender } = render(<DecisionButtons open busy={false} onDecide={onDecide} onReopen={onReopen} />);
    fireEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
    expect(onDecide).toHaveBeenCalledWith({ decision: "reviewed" }, "Marked reviewed");
    fireEvent.click(screen.getByRole("button", { name: "Snooze 30 d" }));
    expect(onDecide.mock.calls[1][0].decision).toBe("snoozed");

    rerender(<DecisionButtons open={false} busy={false} onDecide={onDecide} onReopen={onReopen} />);
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    expect(onReopen).toHaveBeenCalled();
  });
});
