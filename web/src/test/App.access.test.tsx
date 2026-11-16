import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { setCsrfToken } from "../api/api-transport";
import { AuthenticationGate } from "../auth/auth-gate";
import type { Capability } from "../auth/capabilities";
import { completeScan, installProductionApi, json, signedInStatus } from "./app-test-fixtures";

const USER_CAPABILITIES: Capability[] = [
  "profile.view.own", "profile.edit.own", "media.browse",
  "scans.start", "scan_jobs.view.own", "reviews.create.own", "reviews.view.own", "system.version.view",
];

function installUserApi() {
  const fallback = installProductionApi();
  const paths: string[] = [];
  const request = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const { pathname } = new URL(String(input), "http://editarr.test");
    paths.push(pathname);
    if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, username: "member", role: "user", capabilities: USER_CAPABILITIES }));
    if (pathname.startsWith("/api/settings/") || pathname.startsWith("/api/backups") || pathname === "/api/system/restart") {
      return json({ code: "capability_denied", detail: "Not allowed" }, 403);
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", request);
  return paths;
}

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.location.hash = "";
});

describe("capability-aware application shell", () => {
  it.each([true, false])("keeps active item scans only with queue visibility %s", async (mayViewJobs) => {
    const fallback = installProductionApi();
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const { pathname } = new URL(String(input), "http://editarr.test");
      paths.push(pathname);
      if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, role: "user", capabilities: [
        "media.browse", "scans.findings.read", ...(mayViewJobs ? ["scan_jobs.view.own" as const] : []),
      ] }));
      if (pathname === "/api/scans/history") return json([]);
      if (pathname === "/api/scans/queue") return json([
        { ...completeScan({ name: "Active item job", status: "scanning" }), queue_id: 9, sort_order: 0, progress_percent: 20 },
        { ...completeScan({ id: "unrelated", jellyfin_item_id: "movie-2", name: "Another item job", status: "scanning" }), queue_id: 10, sort_order: 1 },
      ]);
      return fallback(input, init);
    }));
    window.location.hash = "#/library/movies/item/movie-1";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Example Movie" })).toBeVisible();
    if (mayViewJobs) expect(await screen.findByText(/Active item job.*scanning/)).toBeVisible();
    else {
      expect(await screen.findByText("No scans yet. Create a new scan to begin.")).toBeVisible();
      expect(paths).not.toContain("/api/scans/queue");
    }
    expect(screen.queryByText(/Another item job.*scanning/)).not.toBeInTheDocument();
  });

  it("omits scan submission and queue requests when those grants are denied", async () => {
    const fallback = installProductionApi();
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const { pathname } = new URL(String(input), "http://editarr.test");
      paths.push(pathname);
      if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, role: "user", capabilities: ["media.browse", "system.version.view"] }));
      return fallback(input, init);
    }));
    window.location.hash = "#/library/movies/item/movie-1";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Example Movie" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "New Scan" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open scans queue and history" })).not.toBeInTheDocument();
    expect(paths).not.toContain("/api/capabilities");
    expect(paths).not.toContain("/api/scans/queue");
    expect(paths).not.toContain("/api/scans/history");
    expect(paths.filter((path) => /\/models|\/settings\//.test(path))).toEqual([]);
  });

  it("clears revoked media, results, and an open track selection on a permission revision", async () => {
    const fallback = installProductionApi();
    let revision = 1;
    let finishTracks!: (response: Response) => void;
    const tracks = new Promise<Response>((resolve) => { finishTracks = resolve; });
    let tracksRequested = false;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const { pathname } = new URL(String(input), "http://editarr.test");
      if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, role: "user", revision, capabilities: [...USER_CAPABILITIES, "scans.findings.read"] }));
      if (pathname === "/api/library-items/movie-1" && revision > 1) return json({ code: "not_found", detail: "Media unavailable" }, 404);
      if (pathname === "/api/scans/history") return json(revision === 1 ? [completeScan({ name: "Revoked scan result" })] : []);
      if (pathname === "/api/media/audio-tracks") { tracksRequested = true; return tracks; }
      return fallback(input, init);
    }));
    window.location.hash = "#/library/movies/item/movie-1";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    await screen.findByText(/Revoked scan result.*complete/);
    await waitFor(() => expect(tracksRequested).toBe(true));
    revision = 2;
    fireEvent(window, new Event("focus"));
    await screen.findByText("Media unavailable");
    expect(screen.queryByRole("heading", { name: "Example Movie" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Revoked scan result/)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "New Scan" })).not.toBeInTheDocument();
    await act(async () => { finishTracks(json({ file_version: "stale", tracks: [{ stream_index: 1, title: "Stale track" }], selection: null })); await tracks; });
    expect(screen.queryByText("Stale track")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add to Queue" })).not.toBeInTheDocument();
  });

  it("hides administrator navigation and never asks for connection settings", async () => {
    const paths = installUserApi();
    window.location.hash = "#/settings/general";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    expect(await screen.findByRole("button", { name: "Account menu for member" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "Movies" })).toBeVisible();
    for (const name of ["Connections", "Filters", "Models", "Audit history"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "General" })).toBeVisible();
    expect(paths).not.toContain("/api/settings/jellyfin");
  });

  it("refuses a deep link to an administrator page without rendering it", async () => {
    const paths = installUserApi();
    window.location.hash = "#/settings/connections";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    expect(await screen.findByRole("heading", { name: "Page unavailable" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Connections" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Jellyfin" })).not.toBeInTheDocument();
    expect(paths.filter((path) => path.startsWith("/api/settings/"))).toEqual([]);
  });

  it("refuses the Server defaults tab address to an account with only personal filter capabilities", async () => {
    const fallback = installProductionApi();
    const paths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const { pathname } = new URL(String(input), "http://editarr.test");
      paths.push(pathname);
      if (pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, username: "member", role: "user",
        capabilities: [...USER_CAPABILITIES, "filters.preferences.edit.own", "filters.mode.choose.own"] }));
      return fallback(input, init);
    }));
    window.location.hash = "#/settings/filters/server";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    expect(await screen.findByRole("heading", { name: "Page unavailable" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "For new accounts" })).not.toBeInTheDocument();
    expect(paths.filter(path => path.startsWith("/api/settings/filters"))).toEqual([]);
  });

  it("sends an account without connection access from the root to General settings", async () => {
    installUserApi();
    window.location.hash = "";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    await waitFor(() => expect(window.location.hash).toBe("#/settings/general"));
    expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  });

  it("refuses ordinary-user audit deep links without an audit request", async () => {
    const paths = installUserApi();
    window.location.hash = "#/audit/00000000-0000-4000-8000-000000000001";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    expect(await screen.findByRole("heading", { name: "Page unavailable" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Audit history" })).not.toBeInTheDocument();
    expect(paths.some(path => path.startsWith("/api/audit"))).toBe(false);
  });
});
