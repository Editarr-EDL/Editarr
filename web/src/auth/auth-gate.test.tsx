import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { apiRequest, setCsrfToken } from "../api";
import type { AuthenticationStatus } from "../api/api-system-types";
import { readUserValue, writeUserValue } from "../shared/user-storage";
import { BulkAccess } from "../settings/users/bulk-access";
import { ordinaryAccount } from "../settings/users/users.test-support";
import { installProductionApi, signedInStatus } from "../test/app-test-fixtures";
import { AdministratorLogin } from "./auth-login";
import { signOutThroughMenu } from "./account-menu.test-support";
import {
  AuthenticationGate,
  Can,
  safeInternalHash,
  useAuthenticationGate,
  usePrincipal,
} from "./auth-gate";
import { CAPABILITIES } from "./capabilities";

const signedOut: AuthenticationStatus = {
  state: "unauthenticated", setup_required: false, sign_in: { jellyfin: true }, restart_required: false,
};

function status(state: "setup_required" | "unauthenticated" | "authenticated"): AuthenticationStatus {
  if (state === "authenticated") return signedInStatus();
  return { ...signedOut, state, setup_required: state === "setup_required" };
}

function json(body: unknown, responseStatus = 200): Response {
  return new Response(JSON.stringify(body), {
    status: responseStatus,
    headers: { "Content-Type": "application/json" },
  });
}

function ProtectedProbe({ onPoll }: { onPoll?: () => void }) {
  useEffect(() => {
    const timer = window.setInterval(onPoll ?? (() => undefined), 1000);
    return () => window.clearInterval(timer);
  }, [onPoll]);
  return <h1>Protected application</h1>;
}

function DraftProbe() {
  const [draft, setDraft] = useState("");
  return <>
    <label>Draft<input value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
    <Can capability="connections.view"><button type="button">Connections</button></Can>
  </>;
}

function Accept({ label, next }: { label: string; next: AuthenticationStatus }) {
  const { acceptAuthenticationStatus } = useAuthenticationGate();
  return <button type="button" onClick={() => acceptAuthenticationStatus(next)}>{label}</button>;
}

function RetryStatusProbe() {
  const { retryAuthenticationStatus } = useAuthenticationGate();
  return <button type="button" onClick={retryAuthenticationStatus}>Reload authentication status</button>;
}

function userKeys(): string[] {
  const keys: string[] = [];
  for (let index = 0; index < window.localStorage.length; index += 1) keys.push(window.localStorage.key(index) ?? "");
  return keys.filter((key) => key.startsWith("editarr.user."));
}

const withoutConnections = CAPABILITIES.filter((capability) => capability !== "connections.view");

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.location.hash = "";
  document.title = "Editarr";
});

describe("AuthenticationGate", () => {
  it("keeps mixed bulk receipts through a same-user revision refresh but clears them for a different identity", async () => {
    let currentStatus = signedInStatus({ userId: 2, role: "admin", revision: 1 });
    const writes: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/auth/status") return json(currentStatus);
      if (path.endsWith("/users/3/mode")) { writes.push(path); return json({ revision: 2 }); }
      if (path.endsWith("/users/3")) return json({ revision: 1 });
      currentStatus = signedInStatus({ userId: 2, role: "admin", revision: 2 });
      return json({ code: "capability_denied", detail: "Protected account" }, 403);
    }));
    function BulkProbe() {
      const principal = usePrincipal();
      return principal && <BulkAccess selected={[ordinaryAccount, { ...ordinaryAccount, id: 4, display_name: "Protected" }]} principal={principal} onChanged={() => undefined} />;
    }
    render(<AuthenticationGate><><DraftProbe /><BulkProbe /></></AuthenticationGate>);
    fireEvent.change(await screen.findByLabelText("Draft"), { target: { value: "private draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Set selected filter modes" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "Bulk access results" })).toHaveTextContent("Pat: applied"));
    expect(screen.getByRole("region", { name: "Bulk access results" })).toHaveTextContent("Protected: refused (capability_denied)");
    await waitFor(() => expect(screen.getByLabelText("Draft")).toHaveValue(""));
    expect(writes).toEqual(["/api/settings/filters/users/3/mode"]);
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(screen.getByRole("region", { name: "Bulk access results" })).toHaveTextContent("Pat: applied"));
    currentStatus = signedInStatus({ userId: 5, role: "admin", revision: 1 });
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument());
  });

  it.each(["logout", "disable", "normal-replacement"])("clears a private library title after %s", async (transition) => {
    const fallback = installProductionApi();
    let currentStatus = signedInStatus();
    const privateName = "Owner confidential family recordings";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const pathname = new URL(String(input), "http://editarr.test").pathname;
      if (pathname === "/api/auth/status") return json(currentStatus);
      if (pathname === "/api/libraries") return json([{ id: "movies", name: privateName, collection_type: "movies", locations: [] }]);
      if (pathname === "/api/auth/logout") return json(signedOut);
      if (pathname === "/api/disabled-probe") return json({ code: "account_disabled" }, 403);
      return fallback(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate unauthenticated={<AdministratorLogin />}><App /></AuthenticationGate>);
    await waitFor(() => expect(document.title).toContain(privateName));
    if (transition === "logout") {
      await signOutThroughMenu();
      await screen.findByRole("heading", { name: "SIGN IN TO CONTINUE" });
    } else if (transition === "disable") {
      await act(async () => { await apiRequest("/api/disabled-probe"); });
      await screen.findByRole("heading", { name: "SIGN IN TO CONTINUE" });
    } else {
      currentStatus = signedInStatus({ userId: 2, username: "replacement", role: "user", capabilities: ["profile.view.own"] });
      fireEvent(window, new Event("focus"));
      await screen.findByRole("button", { name: "Account menu for replacement" });
    }
    expect(screen.queryByText(privateName, { exact: true })).not.toBeInTheDocument();
    expect(document.title).not.toContain(privateName);
  });

  it("requests only auth status before deciding, without mounting protected UI", async () => {
    let resolveStatus: ((response: Response) => void) | undefined;
    const request = vi.fn((_input: RequestInfo | URL) => new Promise<Response>((resolve) => { resolveStatus = resolve; }));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);

    expect(request).toHaveBeenCalledTimes(1);
    expect(String(request.mock.calls[0][0])).toBe("/api/auth/status");
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
    expect(screen.getByText("Checking authentication…")).toBeInTheDocument();

    await act(async () => { resolveStatus?.(json(status("authenticated"))); });
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
  });

  it.each(["setup_required", "unauthenticated"] as const)("does not flash protected UI for %s", async (state) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(status(state))));

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);

    expect(await screen.findByRole("heading", {
      name: state === "setup_required" ? "Jellyfin setup required" : "Sign in required",
    })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });

  it("treats an authenticated status without capabilities as signed out", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ ...signedInStatus(), access: null })));

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);

    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });

  it("shows a retryable error without mounting protected UI", async () => {
    const request = vi.fn()
      .mockRejectedValueOnce(new TypeError("Network unavailable"))
      .mockResolvedValueOnce(json(status("authenticated")));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);

    expect(await screen.findByRole("heading", { name: "Unable to check authentication" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(2);
    expect(String(request.mock.calls[1][0])).toBe("/api/auth/status");
  });

  it("unmounts the protected shell and stops polling after a runtime 401", async () => {
    const clearInterval = vi.spyOn(window, "clearInterval");
    const request = vi.fn()
      .mockResolvedValueOnce(json(status("authenticated")))
      .mockResolvedValueOnce(json({ detail: "Sign in again" }, 401));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();

    await act(async () => { await apiRequest("/api/scans"); });
    expect(screen.getByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
    expect(clearInterval).toHaveBeenCalled();
  });

  it("ends the session when a request reports the account disabled", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(status("authenticated")))
      .mockResolvedValueOnce(json({ code: "account_disabled", detail: "This account is disabled" }, 403));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();

    await act(async () => { await apiRequest("/api/scans"); });
    expect(screen.getByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });

  it("unmounts the protected shell and shows setup after a runtime 423", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(status("authenticated")))
      .mockResolvedValueOnce(json({ detail: "Administrator setup is required" }, 423));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();

    await act(async () => { await apiRequest("/api/scans"); });
    expect(screen.getByRole("heading", { name: "Jellyfin setup required" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });

  it("does not let a stale authenticated status remount the shell after session expiry", async () => {
    let resolveStaleStatus: ((response: Response) => void) | undefined;
    const request = vi.fn()
      .mockResolvedValueOnce(json(status("authenticated")))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveStaleStatus = resolve; }))
      .mockResolvedValueOnce(json({ code: "authentication_required", detail: "Sign in again" }, 401));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><><RetryStatusProbe /><ProtectedProbe /></></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Reload authentication status" }));
    expect(screen.getByText("Checking authentication…")).toBeInTheDocument();
    await act(async () => { await apiRequest("/api/scans"); });
    expect(screen.getByRole("heading", { name: "Sign in required" })).toBeInTheDocument();

    await act(async () => { resolveStaleStatus?.(json(status("authenticated"))); });
    expect(screen.getByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });

  it("rereads permissions after a refusal and discards drafts made under the old revision", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce(json(signedInStatus({ revision: 1 })))
      .mockResolvedValueOnce(json({ code: "capability_denied", detail: "Not allowed" }, 403))
      .mockResolvedValueOnce(json(signedInStatus({ revision: 2, capabilities: withoutConnections })));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><DraftProbe /></AuthenticationGate>);
    fireEvent.change(await screen.findByLabelText("Draft"), { target: { value: "unsaved words" } });
    expect(screen.getByRole("button", { name: "Connections" })).toBeInTheDocument();

    await act(async () => { await apiRequest("/api/settings/jellyfin"); });

    await waitFor(() => expect(screen.queryByRole("button", { name: "Connections" })).not.toBeInTheDocument());
    expect(screen.getByLabelText("Draft")).toHaveValue("");
    expect(String(request.mock.calls[2][0])).toBe("/api/auth/status");
  });

  it("never reopens a hidden control from an older revision answered late", async () => {
    let resolveOld!: (response: Response) => void;
    const request = vi.fn()
      .mockResolvedValueOnce(json(signedInStatus({ revision: 3, capabilities: withoutConnections })))
      .mockResolvedValueOnce(json({ code: "permission_revision_stale", detail: "Reload" }, 409))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }));
    vi.stubGlobal("fetch", request);

    render(<AuthenticationGate><DraftProbe /></AuthenticationGate>);
    expect(await screen.findByLabelText("Draft")).toBeInTheDocument();

    await act(async () => { await apiRequest("/api/scans"); });
    await act(async () => { resolveOld(json(signedInStatus({ revision: 2 }))); });

    expect(screen.queryByRole("button", { name: "Connections" })).not.toBeInTheDocument();
  });

  it("clears the previous account's storage and route when another account signs in", async () => {
    window.location.hash = "#/settings/filters";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(signedInStatus({ userId: 1 }))));
    function Shell() {
      useEffect(() => { writeUserValue("draft", "first user's draft"); }, []);
      return <>
        <h1>Protected application</h1>
        <Accept label="Sign out" next={signedOut} />
      </>;
    }
    render(<AuthenticationGate unauthenticated={<>
      <Accept label="Sign in as second" next={signedInStatus({ userId: 2, username: "second", role: "user" })} />
      <Accept label="Sign in as first" next={signedInStatus({ userId: 1 })} />
    </>}><Shell /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(readUserValue("draft")).toBe("first user's draft");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(userKeys()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Sign in as second" }));

    expect(await screen.findByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/");
    expect(userKeys()).toEqual(["editarr.user.2.draft"]);
  });

  it("returns the same account to its route after signing back in", async () => {
    window.location.hash = "#/settings/filters";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(signedInStatus({ userId: 1 }))));
    render(<AuthenticationGate unauthenticated={<Accept label="Sign in as first" next={signedInStatus({ userId: 1 })} />}>
      <h1>Protected application</h1><Accept label="Sign out" next={signedOut} />
    </AuthenticationGate>);
    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    fireEvent.click(screen.getByRole("button", { name: "Sign in as first" }));
    expect(window.location.hash).toBe("#/settings/filters");
  });

  it("restores an internal attempted hash only after authentication", async () => {
    window.location.hash = "#/library/movies/item/example";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(status("unauthenticated"))));

    render(
      <AuthenticationGate unauthenticated={<Accept label="Complete authentication" next={status("authenticated")} />}>
        <ProtectedProbe />
      </AuthenticationGate>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Complete authentication" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Protected application" })).toBeInTheDocument());
    expect(window.location.hash).toBe("#/library/movies/item/example");
  });

  it("does not restore a stale route after navigation to the root while status loads", async () => {
    window.location.hash = "#/settings";
    let resolveStatus!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveStatus = resolve; })));
    render(<AuthenticationGate><ProtectedProbe /></AuthenticationGate>);

    await act(async () => {
      window.location.hash = "#/";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    await act(async () => { resolveStatus(json(status("authenticated"))); });

    expect(screen.getByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(window.location.hash).toBe("#/");
  });

  it("accepts only known internal hashes for later restoration", () => {
    expect(safeInternalHash("#/settings/general")).toBe("#/settings/general");
    expect(safeInternalHash("#library/movies/item/example")).toBe("#library/movies/item/example");
    expect(safeInternalHash("https://untrusted.example/#/settings/general")).toBeNull();
    expect(safeInternalHash("#//untrusted.example")).toBeNull();
    expect(safeInternalHash("#https://untrusted.example")).toBeNull();
    expect(safeInternalHash("#javascript:alert(1)")).toBeNull();
  });
});
