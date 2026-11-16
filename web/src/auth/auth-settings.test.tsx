import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { setCsrfToken } from "../api";
import { AuthenticationGate } from "./auth-gate";
import { openAccountMenu, signOutThroughMenu } from "./account-menu.test-support";
import { signedInStatus } from "../test/app-test-fixtures";

function status(overrides: Record<string, unknown> = {}, account: Parameters<typeof signedInStatus>[0] = {}) {
  return { ...signedInStatus({ username: "Admin", ...account }), ...overrides };
}

function json(body: unknown, responseStatus = 200) {
  return new Response(JSON.stringify(body), {
    status: responseStatus,
    headers: { "Content-Type": "application/json" },
  });
}

function deploymentConfiguration() {
  const snapshot = {
    base_url: "http://localhost:8000/",
    public_origin: "http://localhost:8000",
    prefix: "/",
    listening_port: 8000,
    base_url_source: "local_default",
    port_source: "default",
    cli_port_override: null,
  };
  return {
    active: snapshot,
    next_start: snapshot,
    saved: { base_url: null, port: null },
    restart_required: false,
    restart: {
      supported: false,
      mode: "unsupported",
      generation: 0,
      in_progress: false,
      outcome: "idle",
      target_generation: null,
      reason: "controller_unavailable",
      detail: "Restart Editarr is unavailable for this launch mode",
    },
  };
}

function installApi(authenticationStatus: Record<string, unknown>) {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/auth/status") return json(authenticationStatus);
    if (url.pathname === "/api/backups/schedule") return json({
      config: null, next_run: null, last_attempt: null, last_success: null,
      outcome: "idle", error: null, retained_count: 0, restore_hold: false,
    });
    if (url.pathname === "/api/settings/jellyfin") return json({
      server_url: null, api_key_configured: false, last_checked_at: null, last_error: null,
    });
    if (url.pathname === "/api/scans/queue") return json([]);
    if (url.pathname === "/api/scans/history") return json([]);
    if (url.pathname === "/api/settings/path-mappings") return json([]);
    if (url.pathname === "/api/settings/deployment") return json(deploymentConfiguration());
    if (url.pathname === "/api/auth/logout" && init?.method === "POST") {
      return json(status({ state: "unauthenticated", csrf_token: null }));
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
}

function renderAuthenticatedApp() {
  return render(<AuthenticationGate><App /></AuthenticationGate>);
}

describe("general settings sign-out", () => {
  beforeEach(() => {
    window.location.hash = "#/settings/general";
  });

  afterEach(() => {
    cleanup();
    setCsrfToken(null);
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("offers no local username or password change and no control that relaxes sign-in", async () => {
    vi.stubGlobal("fetch", installApi(status()));
    renderAuthenticatedApp();

    const menu = await openAccountMenu();
    expect(within(menu).getByRole("menuitem", { name: "Sign Out" })).toBeVisible();
    expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save (username|password)/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByText(/Authentication requirement/i)).not.toBeInTheDocument();
  });

  it("sends the in-memory CSRF token when signing out and returns to the login boundary", async () => {
    const fetchMock = installApi(status());
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    await signOutThroughMenu();

    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeVisible();
    await waitFor(() => {
      const logoutCall = fetchMock.mock.calls.find(([input]) => new URL(String(input), "http://editarr.test").pathname === "/api/auth/logout");
      expect(logoutCall?.[1]).toMatchObject({ method: "POST" });
      expect(new Headers(logoutCall?.[1]?.headers).get("X-CSRF-Token")).toBe("A".repeat(43));
    });
  });

  it("tears down the protected queue poller after logout", async () => {
    const fetchMock = installApi(status());
    const clearTimeout = vi.spyOn(window, "clearTimeout");
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    await screen.findByRole("button", { name: /^Account menu for / });
    await waitFor(() => expect(fetchMock.mock.calls.filter(([input]) =>
      new URL(String(input), "http://editarr.test").pathname === "/api/scans/queue",
    )).toHaveLength(1));
    await signOutThroughMenu();
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeVisible();
    expect(clearTimeout).toHaveBeenCalled();
  });
});
