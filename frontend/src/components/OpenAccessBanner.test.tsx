import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

let status = "disabled";
vi.mock("@/auth/AuthProvider", () => ({ useAuth: () => ({ status }) }));

import OpenAccessBanner, { isThisMachine } from "./OpenAccessBanner";

const show = (hostname: string) =>
  render(
    <MemoryRouter>
      <OpenAccessBanner hostname={hostname} />
    </MemoryRouter>,
  );

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  status = "disabled";
});

describe("isThisMachine", () => {
  it("knows this machine's own names", () => {
    for (const h of ["localhost", "LOCALHOST", "app.localhost", "127.0.0.1", "127.1.2.3", "::1", "[::1]"]) {
      expect(isThisMachine(h)).toBe(true);
    }
  });
  it("treats every other name or address as the network", () => {
    for (const h of ["192.168.1.20", "mymac.local", "rbaclens.lab.example", "localhost.evil.example", "1270.0.0.1"]) {
      expect(isThisMachine(h)).toBe(false);
    }
  });
});

describe("OpenAccessBanner", () => {
  it("warns when sign-in is off and the page was opened by a network address", () => {
    show("192.168.1.20");
    expect(screen.getByRole("alert").textContent).toContain("Sign-in is off");
    expect(screen.getByRole("link", { name: "Turn sign-in on" }).getAttribute("href")).toBe("/settings/authentication");
  });

  it("says nothing on this machine's own address", () => {
    show("localhost");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says nothing when sign-in is on, or not known yet", () => {
    for (const s of ["signed-in", "signed-out", "loading", "error"]) {
      status = s;
      show("192.168.1.20");
      expect(screen.queryByRole("alert")).toBeNull();
      cleanup();
    }
  });

  it("can be hidden, and stays hidden for the browser session", () => {
    show("192.168.1.20");
    fireEvent.click(screen.getByRole("button", { name: "Hide for now" }));
    expect(screen.queryByRole("alert")).toBeNull();
    cleanup();
    show("192.168.1.20");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
