import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AuthenticationGate } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { setCsrfToken } from "../api/api-transport";
import { json, signedInStatus } from "../test/app-test-fixtures";
import { SettingsView, type SettingsSection } from "./settings-view";

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const routePermissions: Record<string, readonly Capability[]> = {
  "GET /api/auth/api-key": ["profile.view.own"],
  "GET /api/capabilities": ["scans.start"],
  "GET /api/community/connection": ["community.identity.view.own"],
  "GET /api/community/connection/status": ["community.identity.view.own"],
  "GET /api/community/keys/recovery": ["community.identity.view.own"],
  "POST /api/community/enrollment/start": ["community.identity.enroll.own"],
  "POST /api/community/keys/rotation-status": ["community.identity.keys.own"],
  "POST /api/community/keys/recovery/check": ["community.identity.keys.own"],
  "GET /api/models": ["models.inventory.view"],
  "GET /api/settings/path-mappings": ["path_mappings.view", "media.paths.view"],
  "POST /api/settings/path-mappings": ["path_mappings.create", "media.paths.view"],
  "POST /api/settings/path-mappings/resolve": ["path_mappings.test", "media.paths.view"],
};

function serve(section: SettingsSection, capabilities: Capability[], bodies: Record<string, unknown> = {}, navigate = false) {
  const calls: { request: string; status: number }[] = [];
  const granted = ["profile.view.own" as const, ...capabilities];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://editarr.test").pathname;
    if (path === "/api/auth/status") return json(signedInStatus({ userId: 2, username: "reviewer", role: "admin", capabilities: granted }));
    const request = `${init?.method ?? "GET"} ${path}`;
    const required = routePermissions[request];
    const body = await bodies[request];
    const status = required && required.every(capability => granted.includes(capability))
      ? body instanceof Response ? body.status : 200 : 403;
    calls.push({ request, status });
    return status === 403 ? json({ code: "capability_denied", detail: "Forbidden" }, status)
      : body instanceof Response ? body : json(body ?? {});
  }));
  function SettingsNavigation() {
    const [current, setCurrent] = useState(section);
    return <><button onClick={() => setCurrent("connections")}>Open connections</button><button onClick={() => setCurrent("general")}>Open general</button><SettingsView section={current} settings={null} onSaved={async () => {}} /></>;
  }
  render(<AuthenticationGate>{navigate ? <SettingsNavigation /> : <SettingsView section={section} settings={null} onSaved={async () => {}} />}</AuthenticationGate>);
  return calls;
}

it("R1 mapping test remains usable without permission to list every mapping", async () => {
  const calls = serve("general", ["path_mappings.test", "media.paths.view"], {
    "POST /api/settings/path-mappings/resolve": { mapping_id: null, local_path: "/synthetic/movie.mkv" },
  });
  await screen.findByRole("heading", { name: "General" });
  expect(screen.queryByLabelText("Test a remote path")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Test a remote path"), { target: { value: "/synthetic/movie.mkv" } });
  fireEvent.click(screen.getByRole("button", { name: "Test resolution" }));
  expect(await screen.findByText("No mapping applies; scanner will use /synthetic/movie.mkv")).toBeVisible();
  expect(calls).toContainEqual({ request: "POST /api/settings/path-mappings/resolve", status: 200 });
  expect(calls.some(call => call.request === "GET /api/settings/path-mappings")).toBe(false);
});

it("R1 mapping creation does not read the list without list permission", async () => {
  const calls = serve("general", ["path_mappings.create", "media.paths.view"], {
    "POST /api/settings/path-mappings": { id: 1, remote_path: "/remote", local_path: "/local" },
  });
  await screen.findByRole("heading", { name: "General" });
  expect(screen.queryByRole("button", { name: "Add mapping" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Remote path", { exact: true }), { target: { value: "/remote" } });
  fireEvent.change(screen.getByLabelText("Local path", { exact: true }), { target: { value: "/local" } });
  fireEvent.click(screen.getByRole("button", { name: "Add mapping" }));
  expect(await screen.findByText("Path mapping added.")).toBeVisible();
  expect(calls).toContainEqual({ request: "POST /api/settings/path-mappings", status: 200 });
  expect(calls.some(call => call.request === "GET /api/settings/path-mappings")).toBe(false);
  expect(screen.queryByRole("region", { name: "Path mappings table" })).not.toBeInTheDocument();
});

it.each([false, true])("mapping feedback stays scoped to General when completion after navigation=%s", async late => {
  let complete!: (value: unknown) => void;
  const pending = new Promise(resolve => { complete = resolve; });
  const mapping = { id: 1, remote_path: "/remote", local_path: "/local" };
  serve("general", ["path_mappings.create", "media.paths.view", "connections.view"], {
    "POST /api/settings/path-mappings": late ? pending : mapping,
  }, true);
  await screen.findByRole("heading", { name: "General" });
  fireEvent.change(screen.getByLabelText("Remote path", { exact: true }), { target: { value: "/remote" } });
  fireEvent.change(screen.getByLabelText("Local path", { exact: true }), { target: { value: "/local" } });
  fireEvent.click(screen.getByRole("button", { name: "Add mapping" }));
  if (!late) expect(await screen.findByText("Path mapping added.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open connections" }));
  await screen.findByRole("heading", { name: /^Jellyfin$/ });
  if (late) {
    await act(async () => { complete(mapping); await pending; });
  }
  expect(screen.queryByText("Path mapping added.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open general" }));
  expect(await screen.findByText("Path mapping added.")).toBeVisible();
});

it("R2 identity-view-only panel hides unauthorized enrollment", async () => {
  const calls = serve("connections", ["connections.view", "community.identity.view.own"], {
    "GET /api/community/connection/status": { configured: true, connected: false, serviceUrl: "https://community.test", identity: null, profile: null, pending: null },
    "GET /api/community/keys/recovery": { configured: true, identity: null, pending: null, completed: null },
  });
  await screen.findByText("Not connected", { exact: true });
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh connection" })).toBeEnabled());
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
  expect(calls.filter(call => call.status === 403)).toEqual([]);
});

const identity = { installationId: "installation-1", keyId: "sha256:" + "a".repeat(64), contributorId: "contributor-001", lastKnownKeyState: "active" };
const profile = { contributorId: identity.contributorId, shortId: "contribu", installationId: identity.installationId,
  keyId: identity.keyId, keyState: "active", displayName: "Cedar Frost Grove", profileRevision: 1,
  policyVersion: "test", protocolVersion: 1, allowedOperations: ["identity:read", "identity:rename"] };

it.each([false, true])("R2 pending enrollment controls require enrollment permission=%s", async enroll => {
  serve("connections", ["connections.view", "community.identity.view.own", ...(enroll ? ["community.identity.enroll.own" as const] : [])], {
    "GET /api/community/connection/status": { configured: true, connected: false, serviceUrl: "https://community.test", identity: null, profile: null,
      pending: { stateId: "b".repeat(64), expiresAt: 1900000000, awaitingConfirmation: true } },
    "GET /api/community/keys/recovery": { configured: true, identity: null, pending: null, completed: null },
  });
  await screen.findByText("Awaiting confirmation", { exact: true });
  expect(screen.queryByRole("button", { name: "Retry confirmation" }) !== null).toBe(enroll);
  expect(screen.queryByRole("button", { name: "Cancel enrollment" }) !== null).toBe(enroll);
});

it.each([false, true])("R2 connected profile and key controls follow their own grants=%s", async mutate => {
  const connection = { configured: true, connected: true, serviceUrl: "https://community.test", identity, profile, pending: null };
  const calls = serve("connections", ["connections.view", "community.identity.view.own", ...(mutate ? ["community.identity.profile.own", "community.identity.keys.own"] as const : [])], {
    "GET /api/community/connection/status": connection,
    "GET /api/community/connection": connection,
    "GET /api/community/keys/recovery": { configured: true, identity, pending: null, completed: null },
  });
  await screen.findByRole("heading", { name: "Contributor profile" });
  if (mutate) await screen.findByRole("heading", { name: "Lost-key recovery" });
  else {
    expect(screen.queryByRole("heading", { name: "Lost-key recovery" })).not.toBeInTheDocument();
    expect(calls.some(call => call.request === "GET /api/community/keys/recovery")).toBe(false);
  }
  expect(screen.queryByLabelText("Contributor name") !== null).toBe(mutate);
  for (const name of ["Save name", "Rotate key", "Recover with Discord"]) {
    expect(screen.queryByRole("button", { name }) !== null).toBe(mutate);
  }
  expect(screen.getByText(identity.contributorId)).toBeVisible();
});

it.each(["community.identity.profile.own", "community.identity.keys.own"] as const)("R2 pending mutations require their own grant, not %s alone", async grant => {
  const connection = { configured: true, connected: true, serviceUrl: "https://community.test", identity, profile, pending: null,
    pendingRename: { display_name: "Pending Grove", operation_id: "rename-1" },
    pendingRotation: { operationId: "rotation-1", newKeyId: "new-key", state: "pending" } };
  const recovery = { configured: true, serviceUrl: "https://community.test", identity, completed: null,
    pending: { operationId: "recovery-1", stateId: "b".repeat(64), oldKeyId: identity.keyId, newKeyId: "new-key" } };
  const calls = serve("connections", ["connections.view", "community.identity.view.own", grant], {
    "GET /api/community/connection/status": connection,
    "GET /api/community/connection": connection,
    "GET /api/community/keys/recovery": recovery,
    "POST /api/community/keys/rotation-status": connection,
    "POST /api/community/keys/recovery/check": recovery,
  });
  if (grant === "community.identity.keys.own") await screen.findByText("Saved recovery operation:", { exact: false });
  else {
    await screen.findByRole("heading", { name: "Contributor profile" });
    expect(screen.queryByText("Saved recovery operation:", { exact: false })).not.toBeInTheDocument();
    expect(calls.some(call => call.request === "GET /api/community/keys/recovery")).toBe(false);
  }
  for (const name of ["Retry name change", "Discard pending name change"]) {
    expect(screen.queryByRole("button", { name }) !== null).toBe(grant === "community.identity.profile.own");
  }
  for (const name of ["Check rotation", "Retry saved rotation", "Review recovery", "Check recovery", "Retry saved recovery"]) {
    expect(screen.queryByRole("button", { name }) !== null).toBe(grant === "community.identity.keys.own");
  }
  if (grant === "community.identity.keys.own") {
    fireEvent.click(screen.getByRole("button", { name: "Check rotation" }));
    await waitFor(() => expect(calls).toContainEqual({ request: "POST /api/community/keys/rotation-status", status: 200 }));
    fireEvent.click(screen.getByRole("button", { name: "Check recovery" }));
    await waitFor(() => expect(calls).toContainEqual({ request: "POST /api/community/keys/recovery/check", status: 200 }));
  }
  expect(calls.filter(call => call.status === 403)).toEqual([]);
});

it.each([false, true])("R3 model cancel and removal require inventory and action grants, inventory=%s", async inventory => {
  const calls = serve("models", ["models.download", "models.download.cancel", "models.delete", ...(inventory ? ["models.inventory.view" as const] : [])], {
    "GET /api/models": { model_capabilities: [], artifacts: ["downloading", "ready"].map(state => ({
      artifact_id: state, backend_ids: [state], source_url: "https://models.test", removable: true,
      state: { state, bytes_downloaded: null, bytes_total: null, error_detail: null },
    })) },
  });
  if (inventory) fireEvent.click(await screen.findByText("Supporting Packages"));
  else await screen.findByRole("heading", { name: "Download models" });
  expect(calls.some(call => call.request === "GET /api/models")).toBe(inventory);
  expect(screen.queryByRole("button", { name: "Cancel download" }) !== null).toBe(inventory);
  expect(screen.queryByRole("button", { name: "Remove" }) !== null).toBe(inventory);
});

it("R4 refused catalog read has an unavailable state, not an empty download form", async () => {
  const calls = serve("models", ["models.download", "scans.start"], {
    "GET /api/capabilities": json({ code: "capability_denied", detail: "Forbidden" }, 403),
  });
  expect(await screen.findByText(/Model catalog unavailable/)).toBeVisible();
  expect(screen.queryByRole("combobox", { name: "Model" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();
  expect(calls).toContainEqual({ request: "GET /api/capabilities", status: 403 });
});
