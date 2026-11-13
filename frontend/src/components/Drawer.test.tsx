import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import Drawer from "./Drawer";

function Page() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open the role</button>
      {open && (
        <Drawer title="Reader" onClose={() => setOpen(false)}>
          <a href="/roles">Role guide</a>
        </Drawer>
      )}
    </>
  );
}

afterEach(cleanup);

describe("Drawer", () => {
  it("takes the focus, keeps Tab inside, and gives the focus back when closed", () => {
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Open the role" });
    opener.focus();
    fireEvent.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Reader" });
    const close = screen.getByRole("button", { name: "Close" });
    const link = screen.getByRole("link", { name: "Role guide" });
    expect(document.activeElement).toBe(close);

    link.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(close); // from the last control back to the first
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(link);
    expect(dialog.contains(document.activeElement)).toBe(true);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
