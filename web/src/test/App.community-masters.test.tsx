import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { MediaDetail } from "../catalog/media-detail";
import { masterFilter, masterLink, masterView } from "../scans/community-master-test-fixtures";
import { installProductionApi, json, mediaItem } from "./app-test-fixtures";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.location.hash = ""; });

const notification = { notificationId: "n1", linkId: "link-1", libraryId: "movies", itemId: "movie-1", mediaSourceId: "source-1", title: "The Matrix",
  kind: "changed", fromRevision: 12, toRevision: 13, createdAt: 1795553648, read: false };
const syncStatus = { version: 1, ran: true, state: "idle", lastRunAt: 1795553648, nextDueAt: 1795640048, lastError: null,
  unreadCount: 1, notifications: [notification] };

it("requests the daily sync on load and links updated items to the notified version", async () => {
  const baseApi = installProductionApi();
  const calls: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname.startsWith("/api/community/sync")) {
      calls.push({ path: url.pathname + url.search, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    }
    if (url.pathname === "/api/community/sync/run") return json(syncStatus);
    if (url.pathname === "/api/community/sync/notifications/read") return new Response(null, { status: 204 });
    if (url.pathname === "/api/editarr-items/legacy/movie-1") return json({ title_id: "title-1", name: "The Matrix",
      active_item: null, active_items: [], versions: [], selected_version_id: "other-version", choose_version: true,
      current_versions: [{ item_id: "movie-1", media_source_id: "source-1", version_id: "ver-1" }] });
    return baseApi(input, init);
  }));
  render(<AuthenticationGate><App /></AuthenticationGate>);
  const entry = await screen.findByRole("button", { name: /Filter updates/ });
  expect(within(entry).getByLabelText("1 unread")).toBeInTheDocument();
  expect(calls.filter((call) => call.path === "/api/community/sync/run")).toEqual([{ path: "/api/community/sync/run", method: "POST", body: {} }]);
  fireEvent.click(entry);
  const link = screen.getByRole("link", { name: /The Matrix/ });
  expect(link).toHaveAttribute("href", "#/library/movies/item/movie-1/source/source-1/community");
  fireEvent.click(link);
  // The item page resolves the notified media source to its exact Editarr version, on its Community tab.
  await waitFor(() => expect(window.location.hash).toBe("#title/title-1/version/ver-1/community"));
  expect(calls).toContainEqual({ path: "/api/community/sync/notifications/read", method: "POST", body: { notification_ids: ["n1"] } });
});

it("shows the read-only Community tab: filters, votes and the update check, with no decisions or update review", async () => {
  const baseApi = installProductionApi();
  const checks: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/community/masters" && url.searchParams.get("item_id") === "movie-1") {
      return json({ version: 1, links: [masterLink()] });
    }
    if (url.pathname === "/api/community/masters/link-1") return json(masterView([masterFilter({ locallyVerified: true })]));
    if (url.pathname === "/api/community/masters/link-1/check") {
      checks.push(init?.method ?? "GET");
      return json({ version: 1, changed: true, latestRevision: 13 });
    }
    return baseApi(input, init);
  }));
  render(<AuthenticationGate><MediaDetail item={mediaItem} breadcrumbs={[]} onNavigate={vi.fn()} onDirtyChange={vi.fn()}
    initialStep="community" /></AuthenticationGate>);
  expect(await screen.findByRole("tab", { name: "Community" })).toHaveAttribute("aria-selected", "true");
  const panel = await screen.findByRole("region", { name: "Community filters" });
  fireEvent.click(await within(panel).findByRole("button", { name: "Show filters" }));
  expect(await within(panel).findByRole("button", { name: /^Thumbs up/ })).toBeInTheDocument();
  expect(within(panel).getByRole("checkbox", { name: "Hide downvoted filters" })).toBeInTheDocument();
  for (const name of [/^Confirm/, /^Reject/, /^Reset/, "Review"]) expect(within(panel).queryByRole("button", { name })).toBeNull();
  fireEvent.click(within(panel).getByRole("button", { name: "Check for filter updates" }));
  expect(await within(panel).findByText(/Downloaded the latest community filters/)).toBeInTheDocument();
  expect(checks).toEqual(["POST"]);
  expect(within(panel).queryByText(/Community filters updated/)).toBeNull();
});
