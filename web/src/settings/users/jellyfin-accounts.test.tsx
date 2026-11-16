import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as jellyfin from "../../api/api-jellyfin-accounts";
import { JellyfinAccounts } from "./jellyfin-accounts";
import { ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-jellyfin-accounts");

describe("Jellyfin accounts", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: null });
    vi.mocked(jellyfin.listJellyfinAccounts).mockResolvedValue({ users: [{ jellyfin_user_id: "jf-pat", name: "Pat", is_administrator: false, is_disabled: false, user_id: null, blocked: false }, { jellyfin_user_id: "jf-off", name: "Off", is_administrator: false, is_disabled: true, user_id: null, blocked: false }] });
  });
  it("imports selected or all accounts and displays each outcome without claiming complete success", async () => {
    vi.mocked(jellyfin.importJellyfinAccounts).mockResolvedValue({ results: [{ jellyfin_user_id: "jf-pat", name: "Pat", outcome: "imported", user_id: 3 }, { jellyfin_user_id: "jf-off", name: "Off", outcome: "upstream_disabled", user_id: null }] });
    render(<JellyfinAccounts principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Import Jellyfin users" }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Pat" }));
    expect(screen.getByRole("checkbox", { name: /Off/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
    expect(await screen.findByRole("region", { name: "Import results" })).toHaveTextContent("Off: upstream_disabled");
    expect(jellyfin.importJellyfinAccounts).toHaveBeenCalledWith({ user_ids: ["jf-pat"] });
    await waitFor(() => expect(jellyfin.readJellyfinSync).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Import all" }));
    expect(jellyfin.importJellyfinAccounts).toHaveBeenCalledTimes(1);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Import all Jellyfin users?" })).getByRole("button", { name: "Confirm import all" }));
    await waitFor(() => expect(jellyfin.importJellyfinAccounts).toHaveBeenLastCalledWith({ all: true }));
    await waitFor(() => expect(jellyfin.readJellyfinSync).toHaveBeenCalledTimes(3));
  });
  it("shows a running-sync conflict and allows refresh without losing last-success status", async () => {
    vi.mocked(jellyfin.runJellyfinSync).mockRejectedValue(new Error("A Jellyfin sync is already running"));
    render(<JellyfinAccounts principal={{ ...ownerPrincipal, role: "admin" }} onChanged={vi.fn()} />);
    await screen.findByText("No linked accounts.");
    fireEvent.click(screen.getByRole("button", { name: "Sync Jellyfin now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already running");
    expect(screen.getByText("Last completed run: Never")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh sync status" }));
    await waitFor(() => expect(jellyfin.readJellyfinSync).toHaveBeenCalledTimes(3));
  });
  it("shows neither import nor sync for an ordinary profile-only user", () => {
    const { container } = render(<JellyfinAccounts principal={{ ...ownerPrincipal, role: "user", capabilities: new Set(["profile.view.own"]) }} onChanged={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    expect(jellyfin.readJellyfinSync).not.toHaveBeenCalled();
    expect(jellyfin.listJellyfinAccounts).not.toHaveBeenCalled();
  });
  it("names sync results from loaded accounts with a numeric fallback", async () => {
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({
      running: false,
      accounts: [{ user_id: 3, jellyfin_user_id: "jf-pat", name: "Pat", link_state: "verified", upstream_state: null, last_verified_at: null, grace_expires_at: null, last_attempt_at: null, last_outcome: null, last_failure: null }],
      last_run: {
        trigger: "manual", counts: { verified: 2 },
        started_at: "2026-10-24T01:00:00Z", finished_at: "2026-10-24T01:00:01Z",
        results: [3, 99].map((user_id) => ({ user_id, outcome: "verified", link_state: "verified", role: "user", status: "active", failure: null })),
      },
    });
    render(<JellyfinAccounts principal={ownerPrincipal} onChanged={vi.fn()} />);
    const results = await screen.findByRole("region", { name: "Sync results" });
    expect(results).toHaveTextContent("Pat: verified, verified, user, active");
    expect(results).toHaveTextContent("Account 99: verified, verified, user, active");
  });
  it("replaces local sync outcomes when refreshed status reports a newer run", async () => {
    const original: jellyfin.JellyfinSyncRun = {
      trigger: "manual", counts: { verified: 1 },
      started_at: "2026-10-24T01:00:00Z", finished_at: "2026-10-24T01:00:01Z",
      results: [{ user_id: 3, outcome: "verified", link_state: "verified", role: "user", status: "active", failure: null }],
    };
    const newer: jellyfin.JellyfinSyncRun = {
      trigger: "scheduled", counts: { disabled: 1 },
      started_at: "2026-10-24T02:00:00Z", finished_at: "2026-10-24T02:00:01Z",
      results: [{ user_id: 3, outcome: "disabled", link_state: "verified", role: "user", status: "disabled", failure: null }],
    };
    vi.mocked(jellyfin.runJellyfinSync).mockResolvedValue(original);
    render(<JellyfinAccounts principal={ownerPrincipal} onChanged={vi.fn()} />);
    await screen.findByText("No linked accounts.");
    fireEvent.click(screen.getByRole("button", { name: "Sync Jellyfin now" }));
    expect(await screen.findByRole("region", { name: "Sync results" })).toHaveTextContent("verified, verified, user, active");
    await waitFor(() => expect(jellyfin.readJellyfinSync).toHaveBeenCalledTimes(2));
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: newer });
    fireEvent.click(screen.getByRole("button", { name: "Refresh sync status" }));
    await screen.findByText(`Last completed run: ${new Date(newer.finished_at).toLocaleString()}`);
    expect(screen.getByRole("region", { name: "Sync results" })).toHaveTextContent("disabled, verified, user, disabled");
    expect(screen.getByRole("region", { name: "Sync results" })).not.toHaveTextContent("user, active");
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: original });
    fireEvent.click(screen.getByRole("button", { name: "Refresh sync status" }));
    await waitFor(() => expect(jellyfin.readJellyfinSync).toHaveBeenCalledTimes(4));
    expect(screen.getByText(`Last completed run: ${new Date(newer.finished_at).toLocaleString()}`)).toBeVisible();
    expect(screen.getByRole("region", { name: "Sync results" })).toHaveTextContent("disabled, verified, user, disabled");
  });
});