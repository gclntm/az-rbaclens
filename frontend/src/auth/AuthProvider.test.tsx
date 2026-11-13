import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthConfigPublic } from "@/api/client";

let config: AuthConfigPublic;
const loaded = vi.fn();
const created = vi.fn();

vi.mock("@/api/client", async (original) => ({
  ...(await original<typeof import("@/api/client")>()),
  api: { auth: { getConfig: async () => config } },
}));

// Stands in for the sign-in library; ``loaded`` records that the app imported it.
vi.mock("@azure/msal-browser", () => {
  loaded();
  class PublicClientApplication {
    constructor(opts: unknown) {
      created(opts);
    }
    initialize = async () => {};
    handleRedirectPromise = async () => null;
    getAllAccounts = () => [];
    loginRedirect = async () => {};
  }
  class InteractionRequiredAuthError extends Error {}
  return { PublicClientApplication, InteractionRequiredAuthError };
});

import AuthProvider from "./AuthProvider";

beforeEach(() => {
  loaded.mockClear();
  created.mockClear();
});
afterEach(cleanup);

describe("sign-in library", () => {
  it("is not loaded when sign-in is off", async () => {
    config = { enabled: false, tenant_id: null, client_id: null, authority: null };
    render(<AuthProvider>app</AuthProvider>);
    expect(await screen.findByText("app")).toBeTruthy();
    expect(loaded).not.toHaveBeenCalled();
  });

  it("is loaded, and set up for this app, when sign-in is on", async () => {
    const tenant = "00000000-0000-0000-0000-000000000001";
    const client = "00000000-0000-0000-0000-000000000002";
    config = { enabled: true, tenant_id: tenant, client_id: client, authority: `https://login.microsoftonline.com/${tenant}` };
    render(<AuthProvider>app</AuthProvider>);
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeTruthy();
    expect(loaded).toHaveBeenCalled();
    expect(created).toHaveBeenCalledWith(expect.objectContaining({ auth: expect.objectContaining({ clientId: client }) }));
    expect(screen.queryByText("app")).toBeNull();
  });
});
