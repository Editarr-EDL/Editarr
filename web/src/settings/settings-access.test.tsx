import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthenticationGate } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { setCsrfToken } from "../api/api-transport";
import { installProductionApi, json, signedInStatus } from "../test/app-test-fixtures";
import { SettingsView } from "./settings-view";
import type { JellyfinSettings } from "../api/api-media-types";

const configuredJellyfin: JellyfinSettings = { server_url: "http://synthetic.test", api_key_configured: true, last_checked_at: null, last_error: null };

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("never renders or requests operational settings for an ordinary user", async () => {
  const fallback = installProductionApi();
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const { pathname } = new URL(String(input), "http://editarr.test");
    paths.push(pathname);
    if (pathname === "/api/auth/status") return json(signedInStatus({
      userId: 4, username: "member", role: "user",
      capabilities: ["profile.view.own", "system.version.view"],
    }));
    return fallback(input, init);
  }));
  render(<AuthenticationGate><SettingsView section="general" settings={null} onSaved={async () => {}} /></AuthenticationGate>);

  expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Backup and restore" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Deployment" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Diagnostics" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Base path mappings" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Restart Editarr" })).not.toBeInTheDocument();
  expect(paths.filter((path) => path.startsWith("/api/settings/") || path.startsWith("/api/backups") || path.startsWith("/api/models") || path.startsWith("/api/system/logs"))).toEqual([]);
});

it("general settings leave the personal API key to My Profile", async () => {
  const fallback = installProductionApi();
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const { pathname } = new URL(String(input), "http://editarr.test");
    paths.push(pathname);
    if (pathname === "/api/auth/status") return json(signedInStatus({
      userId: 4, username: "member", role: "user",
      capabilities: ["profile.view.own", "api_keys.create.own", "api_keys.reveal.own", "api_keys.rotate.own", "api_keys.revoke.own", "system.version.view"],
    }));
    return fallback(input, init);
  }));
  render(<AuthenticationGate><SettingsView section="general" settings={null} onSaved={async () => {}} /></AuthenticationGate>);

  expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  expect(await screen.findByRole("heading", { name: "About Editarr" })).toBeVisible();
  expect(screen.queryByRole("region", { name: "API Key" })).not.toBeInTheDocument();
  expect(paths).not.toContain("/api/auth/api-key");
});

function renderSettings(section: "filters" | "models" | "connections" | "general", capabilities: Capability[], respond: (path: string, init?: RequestInit) => Response, settings: JellyfinSettings | null = null) {
  const paths: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://editarr.test").pathname;
    paths.push(path);
    if (path === "/api/auth/status") return json(signedInStatus({ userId: 2, username: "operator", role: "admin", capabilities }));
    return respond(path, init);
  }));
  render(<AuthenticationGate><SettingsView section={section} settings={settings} onSaved={async () => {}} /></AuthenticationGate>);
  return paths;
}

const permissionRoutes: Record<string, Capability> = {
  "GET /api/auth/api-key": "profile.view.own",
  "GET /api/settings/deployment": "deployment.view",
  "PUT /api/settings/deployment": "deployment.edit",
  "GET /api/settings/models/scans": "models.inventory.view",
  "PUT /api/settings/models/scans": "deployment.edit",
  "GET /api/system/logs/settings": "logs.view",
  "PUT /api/system/logs/settings": "logs.settings.edit",
  "GET /api/capabilities": "scans.start",
  "GET /api/community/connection": "community.identity.view.own",
  "GET /api/community/connection/status": "community.identity.view.own",
  "GET /api/community/keys/recovery": "community.identity.view.own",
};

it("shows only My filters, without a tab bar, to an account with personal filter capabilities", async () => {
  const paths = renderSettings("filters", ["filters.preferences.edit.own"], path => {
    if (path === "/api/mcf-registry") return json({ families: [] });
    if (path === "/api/settings/filters") return json({ revision: 0, selection_rules: [], severity_defaults: [] });
    if (path === "/api/settings/filters/personal") return json({ user_id: 2, mode: "live",
      mode_locked: false, revision: 0, policy_revision: 0, instance_revision: 0,
      rules: [], severities: [], enforced_rules: [], enforced_severities: [] });
    return json({ code: "capability_denied", detail: "Forbidden" }, 403);
  });
  expect(await screen.findByRole("heading", { name: "When the server defaults change" })).toBeVisible();
  await screen.findByRole("button", { name: "Add my rule" });
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "For new accounts" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Default rules" })).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Keep me in sync" })).toBeDisabled();
  expect(screen.getByRole("note")).toHaveTextContent("Your account can't change this choice.");
  expect(paths.filter(path => path.startsWith("/api/settings/filters")).sort()).toEqual(["/api/settings/filters", "/api/settings/filters/personal"]);
});

function renderPermissionSettings(section: "general" | "models" | "connections", capabilities: Capability[], bodies: Record<string, unknown>) {
  const calls: string[] = [];
  renderSettings(section, capabilities, (path, init) => {
    const request = `${init?.method ?? "GET"} ${path}`;
    const required = permissionRoutes[request];
    const allowed = required !== undefined && capabilities.includes(required);
    calls.push(`${request} ${allowed ? 200 : 403}`);
    return allowed ? json(bodies[request] ?? {}) : json({ detail: { code: "capability_denied", message: "Forbidden" } }, 403);
  });
  return calls;
}

it.each([false, true])("matches deployment snapshot and edit permissions (edit=%s)", async (edit) => {
  const snapshot = { base_url: "https://editarr.test/x", public_origin: "https://editarr.test", prefix: "/x", listening_port: 8000, base_url_source: "environment", port_source: "default", cli_port_override: null };
  const configuration = { active: snapshot, next_start: snapshot, saved: { base_url: null, port: null }, restart_required: false, restart: { supported: false } };
  const calls = renderPermissionSettings("general", ["profile.view.own", "deployment.view", ...(edit ? ["deployment.edit"] as const : [])], {
    "GET /api/settings/deployment": configuration,
    "PUT /api/settings/deployment": { ...configuration, saved: { base_url: null, port: 8100 }, restart_required: true },
  });
  expect(await screen.findByRole("region", { name: "Active now" })).toHaveTextContent("https://editarr.test/x");
  expect(screen.getByRole("region", { name: "Next start" })).toHaveTextContent("8000");
  expect(screen.queryByRole("textbox", { name: /^Listening Port/ }) !== null).toBe(edit);
  expect(screen.queryByRole("button", { name: "Save deployment settings" }) !== null).toBe(edit);
  if (edit) {
    fireEvent.change(screen.getByRole("textbox", { name: /^Listening Port/ }), { target: { value: "8100" } });
    fireEvent.click(screen.getByRole("button", { name: "Save deployment settings" }));
    expect(await screen.findByText("Deployment settings saved. Restart Editarr to apply the pending values.")).toBeVisible();
    expect(calls).toContain("PUT /api/settings/deployment 200");
  }
  expect(calls.filter((call) => call.endsWith("403"))).toEqual([]);
});

it("hides logging controls when edit permission cannot read the current value", async () => {
  const calls = renderPermissionSettings("general", ["profile.view.own", "logs.settings.edit"], {});
  fireEvent.click(await screen.findByRole("button", { name: "Logging settings" }));
  expect(screen.queryByRole("checkbox", { name: "Enable debug logging" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Refresh logs" })).not.toBeInTheDocument();
  expect(calls.filter((call) => call.includes("/api/system/logs"))).toEqual([]);
});

it.each([false, true])("reads the model catalog only with scans.start (start=%s)", async (start) => {
  const calls = renderPermissionSettings("models", ["models.download", ...(start ? ["scans.start"] as const : [])], {
    "GET /api/capabilities": { audio_models: [], video_models: [], qwen_models: [], model_capabilities: [] },
  });
  await screen.findByRole("heading", { name: "Download models" });
  if (start) await waitFor(() => expect(calls).toContain("GET /api/capabilities 200"));
  expect(screen.queryByRole("combobox", { name: "Model" }) !== null).toBe(start);
  expect(screen.queryByRole("textbox", { name: "Model" }) !== null).toBe(!start);
  expect(calls).toEqual(start ? ["GET /api/capabilities 200"] : []);
});

it.each([false, true])("shows own community status independently of connection edits (view=%s)", async (view) => {
  const calls = renderPermissionSettings("connections", ["connections.view", ...(view ? ["community.identity.view.own"] as const : [])], {
    "GET /api/community/connection/status": { configured: false, connected: false, identity: null, profile: null, pending: null, pendingRotation: null },
    "GET /api/community/keys/recovery": { configured: false, identity: null, recovery: null },
  });
  await screen.findByRole("heading", { name: "Jellyfin" });
  expect(screen.queryByRole("heading", { name: "Community scans" }) !== null).toBe(view);
  if (view) await waitFor(() => expect(calls).toContain("GET /api/community/connection/status 200"));
  expect(calls.some((call) => call.includes("/api/community/"))).toBe(view);
  expect(calls.some((call) => call.endsWith("403"))).toBe(false);
});

it("lets an admin choose the starting mode for new accounts without reading filter defaults", async () => {
  let mode = "live";
  const paths = renderSettings("filters", ["filters.instance_mode.edit"], (path, init) => {
    if (path === "/api/settings/filters/default-mode") {
      if (init?.method === "PUT") mode = JSON.parse(String(init.body)).mode;
      return json({ mode });
    }
    throw new Error(`Unexpected protected request ${path}`);
  });
  await waitFor(() => expect(screen.getByRole("radio", { name: "Keep them in sync" })).toBeChecked());
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add rule" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "Give them their own copy" }));
  expect(await screen.findByText("New accounts will get their own copy.")).toBeVisible();
  expect(mode).toBe("copy");
  expect(screen.getByRole("radio", { name: "Give them their own copy" })).toBeChecked();
  expect(paths).not.toContain("/api/settings/filters");
});

it("acquires a model with a nullable status and never reads global inventory", async () => {
  const paths = renderSettings("models", ["models.download", "scans.start"], (path) => {
    if (path === "/api/capabilities") return json({ audio_models: [], video_models: [], qwen_models: [], model_capabilities: [{ id: "synthetic", display_name: "Synthetic model", weight_requirement: "on_demand", availability: "available" }] });
    if (path === "/api/models/prefetch") return json({ status: null, accepted: true, removed: false });
    throw new Error(`Unexpected protected request ${path}`);
  });
  await screen.findByRole("option", { name: "Synthetic model" });
  fireEvent.change(screen.getByLabelText("Model"), { target: { value: "synthetic" } });
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  expect(await screen.findByText("Model download requested.")).toBeVisible();
  expect(paths).not.toContain("/api/models");
  expect(paths).not.toContain("/api/settings/models/scans");
  expect(paths.filter((path) => path.startsWith("/api/models/"))).toEqual(["/api/models/prefetch"]);
});

it.each(["models", "filters"] as const)("refuses the %s deep link when every relevant capability is denied", async (section) => {
  const paths = renderSettings(section, [], () => { throw new Error("Forbidden request"); });
  expect(await screen.findByRole("heading", { name: "Page unavailable" })).toBeVisible();
  expect(paths).toEqual(["/api/auth/status"]);
});

it("keeps connection addresses readable without rendering credential or mutation forms", async () => {
  const paths = renderSettings("connections", ["connections.view"], () => json({}), configuredJellyfin);
  expect(await screen.findByRole("heading", { name: "Jellyfin" })).toBeVisible();
  expect(screen.getByText("http://synthetic.test")).toBeVisible();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/API key/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Test.*connection|Save|Disconnect/ })).not.toBeInTheDocument();
  expect(paths.filter((path) => path.startsWith("/api/settings/"))).toEqual([]);
});

it.each([false, true])("gates connection test, save, and disconnect actions (mutable=%s)", async (mutable) => {
  renderSettings("connections", ["connections.view", ...(mutable ? ["connections.test", "connections.edit", "connections.disconnect"] as const : [])], () => json({}), configuredJellyfin);
  expect(await screen.findByText("API key configured")).toBeVisible();
  for (const name of ["Test connection", "Save", "Disconnect"]) {
    expect(screen.queryByRole("button", { name }) !== null).toBe(mutable);
  }
  expect(screen.queryByLabelText("API key", { exact: true }) !== null).toBe(mutable);
});

it.each([false, true])("gates every mapping mutation while retaining readable rows (mutable=%s)", async (mutable) => {
  renderSettings("general", ["path_mappings.view", "media.paths.view", ...(mutable ? ["path_mappings.create", "path_mappings.update", "path_mappings.delete", "path_mappings.reorder"] as const : [])], () => json([
    { id: 1, remote_path: "/synthetic/first", local_path: "/local/first" },
    { id: 2, remote_path: "/synthetic/second", local_path: "/local/second" },
  ]));
  await screen.findByRole("cell", { name: "/synthetic/first" });
  expect(screen.queryByRole("button", { name: "Add mapping" }) !== null).toBe(mutable);
  for (const name of ["Edit", "Delete"]) expect(screen.queryAllByRole("button", { name })).toHaveLength(mutable ? 2 : 0);
  expect(screen.queryByRole("button", { name: "Move /synthetic/second up" }) !== null).toBe(mutable);
  expect(screen.queryByRole("button", { name: "Move /synthetic/first down" }) !== null).toBe(mutable);
  expect(screen.queryByLabelText("Test a remote path")).not.toBeInTheDocument();
});

it("lets an admin inspect and resolve mappings without create, update, delete, or reorder", async () => {
  const paths = renderSettings("general", ["path_mappings.view", "path_mappings.test", "media.paths.view"], (path) => {
    if (path.endsWith("/resolve")) return json({ mapping_id: 1, local_path: "/synthetic/local/movie.mkv" });
    if (path === "/api/settings/path-mappings") return json([{ id: 1, remote_path: "/synthetic/remote", local_path: "/synthetic/local" }]);
    throw new Error(`Unexpected protected request ${path}`);
  });
  await screen.findByRole("cell", { name: "/synthetic/remote" });
  expect(screen.getByRole("region", { name: "Path mappings table" })).toHaveAttribute("tabindex", "0");
  for (const name of ["Add mapping", "Edit", "Delete", "Move /synthetic/remote up", "Move /synthetic/remote down"]) {
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  }
  fireEvent.change(screen.getByLabelText("Test a remote path"), { target: { value: "/synthetic/remote/movie.mkv" } });
  fireEvent.click(screen.getByRole("button", { name: "Test resolution" }));
  expect(await screen.findByText("Maps to /synthetic/local/movie.mkv")).toBeVisible();
  expect(paths).toContain("/api/settings/path-mappings/resolve");
});

it.each(([[], ["path_mappings.view", "path_mappings.test"]] as Capability[][]).map((capabilities) => ({ capabilities })))("does not request mappings or About when their read capabilities are absent ($capabilities)", async ({ capabilities }) => {
  const paths = renderSettings("general", capabilities, () => { throw new Error("Forbidden request"); });
  await screen.findByRole("heading", { name: "General" });
  expect(screen.queryByRole("heading", { name: "Base path mappings" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "About Editarr" })).not.toBeInTheDocument();
  expect(paths.filter((path) => path.startsWith("/api/settings/") || path === "/api/system/version")).toEqual([]);
});

it.each(([[], ["models.download"], ["models.download.cancel"], ["models.delete"], ["models.download", "models.download.cancel", "models.delete"]] as Capability[][]).map((operations) => ({ operations })))("gates each inventory operation independently ($operations)", async ({ operations }) => {
  const paths = renderSettings("models", ["models.inventory.view", ...operations], (path) => {
    if (path === "/api/settings/models/scans") return json({ audio_padding_seconds: 0.1 });
    return json({ model_capabilities: [], artifacts: ["missing", "downloading", "ready"].map((state) => ({
      artifact_id: state, backend_ids: [state], source_url: "https://models.test", removable: true,
      state: { state, bytes_downloaded: null, bytes_total: null, error_detail: null },
    })) });
  });
  fireEvent.click(await screen.findByText("Supporting Packages"));
  await screen.findByRole("article", { name: "ready model" });
  for (const [capability, name] of [["models.download", "Download"], ["models.download.cancel", "Cancel download"], ["models.delete", "Remove"]] as const) {
    expect(screen.queryByRole("button", { name }) !== null).toBe(operations.includes(capability));
  }
  expect(paths).toContain("/api/models");
});

it.each([false, true])("allows logging inspection independently of mutation and path access (edit=%s)", async (edit) => {
  const capabilities: Capability[] = ["logs.view", ...(edit ? ["logs.settings.edit" as const] : [])];
  const paths = renderSettings("general", capabilities, (path) => {
    if (path === "/api/system/logs/settings") return json({ debug_enabled: false });
    throw new Error(`Unexpected protected request ${path}`);
  });
  fireEvent.click(await screen.findByRole("button", { name: "Logging settings" }));
  await waitFor(() => expect(paths).toContain("/api/system/logs/settings"));
  expect(screen.queryByRole("checkbox", { name: "Enable debug logging" }) !== null).toBe(edit);
  expect(screen.queryByLabelText("Log source")).not.toBeInTheDocument();
  expect(paths.some((path) => path.includes("/tail"))).toBe(false);
});

it.each([false, true])("separates backup creation from download and deletion, and offers no restore (sensitive=%s)", async (sensitive) => {
  const capabilities: Capability[] = ["backups.create", ...(sensitive ? ["backups.download", "backups.delete"] as const : [])];
  renderSettings("general", capabilities, () => json({ id: "synthetic-job", state: "ready", action: "export", kind: "full", inventory: null }));
  fireEvent.click(await screen.findByRole("button", { name: "Create backup" }));
  await screen.findByText(sensitive ? "Backup ready for download." : "Backup created.");
  for (const name of ["Download backup", "Cancel job"]) expect(screen.queryByRole("button", { name }) !== null).toBe(sensitive);
  expect(screen.queryByLabelText("Backup file")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Scheduled backups" })).not.toBeInTheDocument();
});

it.each([
  { view: true, edit: false },
  { view: false, edit: true },
  { view: true, edit: true },
])("gates backup schedule inspection and mutation independently ($view, $edit)", async ({ view, edit }) => {
  const paths = renderSettings("general", [
    ...(view ? ["backups.status.view"] as const : []),
    ...(edit ? ["backups.schedule.edit"] as const : []),
  ], (path) => {
    if (path === "/api/backups/schedule") return json({ config: null, next_run: null, last_attempt: null, last_success: null, retained_count: 0 });
    throw new Error(`Unexpected protected request ${path}`);
  });
  await screen.findByRole("heading", { name: "Scheduled backups" });
  if (view) await screen.findByText("Disabled", { exact: true });
  expect(screen.queryByRole("button", { name: "Refresh status" }) !== null).toBe(view);
  expect(screen.queryByRole("button", { name: "Save schedule" }) !== null).toBe(edit);
  expect(screen.queryByLabelText("Backup destination", { exact: true }) !== null).toBe(edit);
  expect(paths.includes("/api/backups/schedule")).toBe(view);
});
