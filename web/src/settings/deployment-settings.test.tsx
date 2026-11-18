import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { setCsrfToken } from "../api";
import { AuthenticationGate } from "../auth/auth-gate";
import { signedInStatus } from "../test/app-test-fixtures";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function authenticatedStatus() {
  return signedInStatus({ username: "Admin" });
}

function deploymentConfiguration(overrides: Record<string, unknown> = {}) {
  const active = {
    base_url: "https://editarr.example.com/editarr",
    public_origin: "https://editarr.example.com",
    prefix: "/editarr",
    listening_port: 8000,
    base_url_source: "environment",
    port_source: "default",
    cli_port_override: null,
  };
  return {
    active,
    next_start: active,
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
    ...overrides,
  };
}

function installApi(
  initial = deploymentConfiguration(),
  save: (request: Record<string, unknown>) => Response | Promise<Response> = () => json(initial),
  dock: (request?: RequestInit) => Response = () => json(null),
) {
  return vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/auth/status") return json(authenticatedStatus());
    if (url.pathname === "/api/backups/schedule") return json({
      config: null, next_run: null, last_attempt: null, last_success: null,
      outcome: "idle", error: null, retained_count: 0, restore_hold: false,
    });
    if (url.pathname === "/api/settings/jellyfin") return json({
      server_url: null, api_key_configured: false, last_checked_at: null, last_error: null,
    });
    if (url.pathname === "/api/scans/queue" || url.pathname === "/api/scans/history" || url.pathname === "/api/settings/path-mappings") return json([]);
    if (url.pathname === "/api/settings/macos/dock") return dock(init);
    if (url.pathname === "/api/settings/deployment" && init?.method !== "PUT") return json(initial);
    if (url.pathname === "/api/settings/deployment" && init?.method === "PUT") {
      return save(JSON.parse(String(init.body)) as Record<string, unknown>);
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
}

function renderAuthenticatedApp() {
  return render(<AuthenticationGate><App /></AuthenticationGate>);
}

describe("deployment settings", () => {
  beforeEach(() => {
    window.location.hash = "#/settings/general";
  });

  afterEach(() => {
    cleanup();
    setCsrfToken(null);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("places Restart Editarr at the top right of General Settings and reports unsaved drafts", async () => {
    vi.stubGlobal("fetch", installApi());
    renderAuthenticatedApp();

    const restart = await screen.findByRole("button", { name: "Restart Editarr" });
    const header = restart.closest("header");
    expect(header).toHaveClass("general-settings-page-header");
    expect(header).toContainElement(screen.getByRole("heading", { name: "General" }));
    expect(screen.getByText(/still restarts Editarr when no changes are pending/)).toBeVisible();

    fireEvent.change(await screen.findByRole("textbox", { name: /^Base URL/ }), {
      target: { value: "https://editarr.example.com/unsaved" },
    });
    expect(await screen.findByText(/You have unsaved changes/)).toHaveTextContent("these edits will not be applied");
  });

  it("shows the enabled Dock preference and saves when it is turned off", async () => {
    const save = vi.fn((request?: RequestInit) => json(request?.body
      ? JSON.parse(String(request.body))
      : { show_in_dock: true }));
    vi.stubGlobal("fetch", installApi(deploymentConfiguration({ macos_dock_supported: true }), undefined, save));
    renderAuthenticatedApp();

    const summary = (await screen.findByText("macOS", { exact: true })).closest("summary")!;
    expect(summary).toHaveTextContent("Dock icon shown");
    fireEvent.click(summary);
    const toggle = await screen.findByRole("checkbox", { name: "Show app icon in the Dock" });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(save).toHaveBeenLastCalledWith(expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ show_in_dock: false }),
    }));
    expect(summary).toHaveTextContent("Dock icon hidden");
  });

  it("renders server-derived nested path data and keeps active values separate from the pending next start", async () => {
    const nextStart = {
      ...deploymentConfiguration().active,
      base_url: "https://editarr.example.com/media/editarr",
      prefix: "/media/editarr",
      listening_port: 8100,
      base_url_source: "saved",
      port_source: "saved",
    };
    const fetchMock = installApi(deploymentConfiguration({
      next_start: nextStart,
      saved: { base_url: nextStart.base_url, port: 8100 },
      restart_required: true,
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/settings/deployment")).toBe(true));
    expect(await screen.findByRole("textbox", { name: /^Base URL/ })).toBeVisible();
    expect(screen.getByRole("region", { name: "Active now" })).toHaveTextContent("https://editarr.example.com/editarr");
    expect(screen.getByRole("region", { name: "Next start" })).toHaveTextContent("https://editarr.example.com/media/editarr");
    expect(screen.getByText("Restart required to apply the next-start settings.")).toBeVisible();
    expect(screen.getByRole("textbox", { name: /^Base URL/ })).toHaveValue("https://editarr.example.com/media/editarr");
    expect(screen.getByText("/media/editarr", { exact: true })).not.toBeVisible();
    fireEvent.click(screen.getByText("Connection details", { exact: true }));
    const activeConnection = within(screen.getByRole("region", { name: "Active connection" }));
    const nextConnection = within(screen.getByRole("region", { name: "Next connection" }));
    expect(activeConnection.getByText("/editarr", { exact: true })).toBeVisible();
    expect(nextConnection.getByText("/media/editarr", { exact: true })).toBeVisible();
  });

  it("saves only an edited port and does not freeze the environment Base URL seed", async () => {
    const initial = deploymentConfiguration();
    const pending = deploymentConfiguration({
      next_start: { ...initial.active, listening_port: 8123, port_source: "saved" },
      saved: { base_url: null, port: 8123 },
      restart_required: true,
    });
    const fetchMock = installApi(initial, (request) => {
      expect(request).toEqual({ port: 8123 });
      return json(pending);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    await screen.findByRole("textbox", { name: /^Listening Port/ });
    fireEvent.change(screen.getByRole("textbox", { name: /^Listening Port/ }), { target: { value: "8123" } });
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));

    expect(await screen.findByText(/Deployment settings saved. Restart Editarr/)).toBeVisible();
    fireEvent.click(screen.getByText("Connection details", { exact: true }));
    expect(screen.getByText(/Base URL has no saved override/)).toBeVisible();
    const put = fetchMock.mock.calls.find(([input, init]) => new URL(String(input), "http://editarr.test").pathname === "/api/settings/deployment" && init?.method === "PUT");
    expect(new Headers(put?.[1]?.headers).get("X-CSRF-Token")).toBe("A".repeat(43));
  });

  it("lets an operator explicitly persist the displayed seed without changing its text", async () => {
    const initial = deploymentConfiguration();
    const saved = deploymentConfiguration({
      active: initial.active,
      next_start: { ...initial.active, base_url_source: "saved" },
      saved: { base_url: initial.active.base_url, port: null },
    });
    vi.stubGlobal("fetch", installApi(initial, (request) => {
      expect(request).toEqual({ base_url: "https://editarr.example.com/editarr" });
      return json(saved);
    }));
    renderAuthenticatedApp();

    await screen.findByRole("textbox", { name: /^Base URL/ });
    fireEvent.click(screen.getByLabelText("Save the displayed Base URL as an override"));
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));

    fireEvent.click(screen.getByText("Connection details", { exact: true }));
    expect(await screen.findByText(/Saved Base URL override: https:\/\/editarr.example.com\/editarr/)).toBeVisible();
  });

  it("keeps the optional Base URL blank in local mode while a port save updates its effective address", async () => {
    const active = {
      ...deploymentConfiguration().active,
      base_url: "http://localhost:8000",
      public_origin: "http://localhost:8000",
      prefix: "/",
      base_url_source: "local_default",
    };
    const initial = deploymentConfiguration({ active, next_start: active });
    vi.stubGlobal("fetch", installApi(initial, (request) => {
      expect(request).toEqual({ port: 8123 });
      return json(deploymentConfiguration({
        active,
        next_start: {
          ...active,
          base_url: "http://localhost:8123",
          public_origin: "http://localhost:8123",
          listening_port: 8123,
          port_source: "saved",
        },
        saved: { base_url: null, port: 8123 },
        restart_required: true,
      }));
    }));
    renderAuthenticatedApp();

    const baseUrlInput = await screen.findByRole("textbox", { name: /^Base URL/ });
    expect(baseUrlInput).toHaveValue("");
    expect(screen.queryByLabelText("Save the displayed Base URL as an override")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Active now" })).toHaveTextContent("http://localhost:8000");
    fireEvent.change(screen.getByRole("textbox", { name: /^Listening Port/ }), { target: { value: "8123" } });
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));

    expect(await screen.findByText(/Deployment settings saved. Restart Editarr/)).toBeVisible();
    expect(baseUrlInput).toHaveValue("");
    expect(screen.getByRole("region", { name: "Next start" })).toHaveTextContent("http://localhost:8123");
  });

  it("shows a saved root URL and explains when --port masks its saved port", async () => {
    const root = {
      ...deploymentConfiguration().active,
      base_url: "https://editarr.example.com",
      prefix: "/",
      base_url_source: "saved",
      listening_port: 9000,
      port_source: "cli",
      cli_port_override: 9000,
    };
    vi.stubGlobal("fetch", installApi(deploymentConfiguration({
      active: root,
      next_start: root,
      saved: { base_url: root.base_url, port: 8123 },
    })));
    renderAuthenticatedApp();

    expect(await screen.findByText("CLI port override is active.")).toBeVisible();
    expect(screen.getByText(/--port 9000 masks the saved Listening Port/)).toBeVisible();
    fireEvent.click(screen.getByText("Connection details", { exact: true }));
    const nextConnection = within(screen.getByRole("region", { name: "Next connection" }));
    expect(nextConnection.getByText("Base path", { exact: true })).toBeVisible();
    expect(nextConnection.getByText("/", { exact: true })).toBeVisible();
  });

  it("prevents a duplicate save while the first request is pending", async () => {
    let finishSave: (response: Response) => void = () => undefined;
    const pendingSave = new Promise<Response>((resolve) => { finishSave = resolve; });
    const fetchMock = installApi(deploymentConfiguration(), () => pendingSave);
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    const portInput = await screen.findByRole("textbox", { name: /^Listening Port/ });
    fireEvent.change(portInput, { target: { value: "8123" } });
    const saveButton = screen.getByRole("button", { name: "Save deployment settings" });
    fireEvent.click(saveButton);
    fireEvent.click(saveButton);

    await waitFor(() => expect(fetchMock.mock.calls.filter(([input, init]) => (
      new URL(String(input), "http://editarr.test").pathname === "/api/settings/deployment" && init?.method === "PUT"
    ))).toHaveLength(1));
    expect(saveButton).toBeDisabled();

    finishSave(json(deploymentConfiguration({
      next_start: { ...deploymentConfiguration().active, listening_port: 8123, port_source: "saved" },
      saved: { base_url: null, port: 8123 },
      restart_required: true,
    })));
    expect(await screen.findByText(/Deployment settings saved. Restart Editarr/)).toBeVisible();
  });

  it("rejects an invalid port locally without resetting the draft or sending a save", async () => {
    const fetchMock = installApi();
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    const portInput = await screen.findByRole("textbox", { name: /^Listening Port/ });
    fireEvent.change(portInput, { target: { value: "70000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Listening Port must be a whole number from 1 through 65535.");
    expect(portInput).toHaveValue("70000");
    expect(fetchMock.mock.calls.some(([input, init]) => new URL(String(input), "http://editarr.test").pathname === "/api/settings/deployment" && init?.method === "PUT")).toBe(false);
  });

  it("retains dirty inputs and shows a deployment validation failure inline", async () => {
    const fetchMock = installApi(deploymentConfiguration(), () => json({
      code: "deployment_configuration_invalid",
      detail: "Base URL must not contain a query",
    }, 422));
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    await screen.findByRole("textbox", { name: /^Base URL/ });
    fireEvent.change(screen.getByRole("textbox", { name: /^Base URL/ }), { target: { value: "https://editarr.example.com/editarr?bad=true" } });
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Base URL must not contain a query");
    expect(screen.getByRole("textbox", { name: /^Base URL/ })).toHaveValue("https://editarr.example.com/editarr?bad=true");
  });

  it("hands an expired deployment-settings request to the shared authentication boundary", async () => {
    const fetchMock = installApi();
    fetchMock.mockImplementation(async (input: string | URL | Request, init?: RequestInit) => {
      const pathname = new URL(String(input), "http://editarr.test").pathname;
      if (pathname === "/api/settings/deployment") return json({ code: "authentication_required", detail: "Sign in again" }, 401);
      if (pathname === "/api/auth/status") return json(authenticatedStatus());
      if (pathname === "/api/settings/jellyfin") return json({ server_url: null, api_key_configured: false, last_checked_at: null, last_error: null });
      if (pathname === "/api/scans/queue" || pathname === "/api/scans/history" || pathname === "/api/settings/path-mappings") return json([]);
      throw new Error(`Unexpected request: ${pathname} ${init?.method || "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAuthenticatedApp();

    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeVisible();
  });
});
