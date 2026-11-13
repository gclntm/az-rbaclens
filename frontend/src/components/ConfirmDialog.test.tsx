import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { ConfirmProvider, useConfirm } from "./ConfirmDialog";

function Page() {
  const confirm = useConfirm();
  const [answer, setAnswer] = useState("none");
  return (
    <>
      <button
        onClick={async () => setAnswer(String(await confirm({ title: "Delete it?", body: "It cannot be restored.", action: "Delete", danger: true })))}
      >
        Ask
      </button>
      <output>{answer}</output>
    </>
  );
}

function open() {
  render(
    <ConfirmProvider>
      <Page />
    </ConfirmProvider>,
  );
  const opener = screen.getByRole("button", { name: "Ask" });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

afterEach(cleanup);

describe("ConfirmDialog", () => {
  it("asks with a title and a body, starts on Cancel, and answers yes on the action", async () => {
    open();
    const dialog = screen.getByRole("alertdialog", { name: "Delete it?" });
    expect(document.getElementById(dialog.getAttribute("aria-describedby") ?? "")?.textContent).toBe("It cannot be restored.");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Delete" })));
    expect(screen.getByText("true")).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("answers no on Cancel and on Escape, and gives the focus back", async () => {
    const opener = open();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    expect(screen.getByText("false")).toBeTruthy();
    expect(document.activeElement).toBe(opener);

    fireEvent.click(opener);
    await act(async () => fireEvent.keyDown(document, { key: "Escape" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByText("false")).toBeTruthy();
  });
});
