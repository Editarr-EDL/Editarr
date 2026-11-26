import { openReviewSources, openWorkflowStep } from "./review-workflow-test-helpers";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MediaDetail } from "../catalog/media-detail";
import { AuthenticationGate } from "../auth/auth-gate";
import { completeScan, installProductionApi, json, mediaItem, savedReview } from "./app-test-fixtures";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("recovers local scans for Jellyfin and the selected review version", async () => {
  const scan = completeScan({ id: "found-scan", name: "Found local evidence", media_source_id: "source-2", library_id: "movies" });
  const review = savedReview();
  const baseApi = installProductionApi();
  const searches: unknown[] = [];
  const associations: unknown[] = [];
  const historyPaths: (string | null)[] = [];
  let associated = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/filter-candidates/available") return json({ available: true });
    if (url.pathname === "/api/filter-candidates") {
      searches.push(JSON.parse(String(init?.body)));
      return json({ media_item_id: "resolved-media", matches: [{ scan, match_reason: "quick_hash", already_associated: false }] });
    }
    if (url.pathname === "/api/filter-candidates/associations") {
      associations.push(JSON.parse(String(init?.body)));
      associated = true;
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/scans/history") {
      historyPaths.push(url.searchParams.get("source_path"));
      return json(associated ? [scan] : []);
    }
    if (url.pathname.endsWith("/movie-1/reviews")) return json([review]);
    return baseApi(input, init);
  }));
  const secondVersion = {
    ...mediaItem.versions[0], id: "source-2", name: "Alternate release", path: "/media/alternate.mkv",
    duration_seconds: 140, scene_name: "Alternate.Scene", release_group: "OTHER", media_format: "MP4",
  };
  render(<AuthenticationGate><MediaDetail item={{ ...mediaItem, versions: [...mediaItem.versions, secondVersion] }} breadcrumbs={[]} onNavigate={vi.fn()} onDirtyChange={vi.fn()} /></AuthenticationGate>);
  await screen.findByRole("button", { name: "Open review Existing review" });
  expect(searches).toEqual([]);
  fireEvent.change(screen.getByLabelText("Media version"), { target: { value: "source-2" } });
  await openWorkflowStep("Scans");
  fireEvent.click(screen.getByRole("button", { name: "Recover Scans" }));
  const dialog = await screen.findByRole("dialog", { name: "Recover Scans" });
  expect(searches).toEqual([{
    source: "jellyfin", item_id: "movie-1", library_id: "movies", media_source_id: "source-2",
    item_name: "Example Movie", item_type: "Movie", duration_seconds: 140,
    scene_name: "Alternate.Scene", release_group: "OTHER", media_format: "MP4", media_provider_id: null,
  }]);
  expect(within(dialog).getByRole("checkbox", { name: /Associate Found local evidence/ })).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Associate Selected" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Recover Scans" })).not.toBeInTheDocument());
  expect(associations).toEqual([{ media_item_id: "resolved-media", scan_ids: [scan.id] }]);
  expect(await screen.findByRole("status")).toHaveTextContent("Associated 1 scan.");
  expect(await within(screen.getByRole("list", { name: "Scan history" })).findByText(/Found local evidence/)).toBeVisible();
  expect(historyPaths.at(-1)).toBeNull();
  await openWorkflowStep("Review");
  expect(screen.getByRole("button", { name: "Open review Existing review" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "New Review" }));
  fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Reuse found evidence" } });
  fireEvent.click(screen.getByRole("button", { name: "Start new review" }));
  expect(await within(await openReviewSources()).findByRole("checkbox", { name: /Found local evidence/ })).toBeVisible();
});

it("uses item metadata and can search when the catalog withholds all paths", async () => {
  const baseApi = installProductionApi();
  const searches: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input).endsWith("/filter-candidates")) {
      searches.push(JSON.parse(String(init?.body)));
      return json({ media_item_id: "media-1", matches: [] });
    }
    if (new URL(String(input), "http://editarr.test").pathname === "/api/scans/history") {
      return json([completeScan({ id: "other-version", jellyfin_item_id: "movie-1", media_source_id: "source-9" })]);
    }
    if (String(input).endsWith("/filter-candidates/available")) return json({ available: false });
    return baseApi(input, init);
  }));
  const props = { breadcrumbs: [], onNavigate: vi.fn(), onDirtyChange: vi.fn() };
  const { rerender } = render(<AuthenticationGate><MediaDetail {...props} item={{ ...mediaItem, versions: [] }} /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  fireEvent.click(await screen.findByRole("button", { name: "Recover Scans" }));
  const dialog = await screen.findByRole("dialog", { name: "Recover Scans" });
  expect(searches).toEqual([{
    source: "jellyfin", item_id: "movie-1", library_id: "movies", media_source_id: null,
    item_name: "Example Movie", item_type: "Movie", duration_seconds: 120,
    scene_name: null, release_group: null, media_format: "MKV", media_provider_id: null,
  }]);
  expect(within(dialog).getByText("No recoverable scans matched this file.")).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "Associate Selected" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  rerender(<AuthenticationGate><MediaDetail {...props} item={{ ...mediaItem, path: null, versions: [] }} /></AuthenticationGate>);
  fireEvent.click(screen.getByRole("button", { name: "Recover Scans" }));
  await screen.findByRole("dialog", { name: "Recover Scans" });
  expect(searches).toHaveLength(2);
  expect(searches.every((search) => !Object.prototype.hasOwnProperty.call(search, "source_path"))).toBe(true);
});
it("disables Recover Scans with an explanation when no local scan could be recovered", async () => {
  const baseApi = installProductionApi();
  const probes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = new URL(String(input), "http://editarr.test").pathname;
    // A scan of an unrelated item doesn't make anything recoverable here.
    if (path === "/api/scans/history") return json([completeScan({ id: "elsewhere", jellyfin_item_id: "other-item" })]);
    if (path === "/api/filter-candidates/available") { probes.push(JSON.parse(String(init?.body))); return json({ available: false }); }
    if (path === "/api/filter-candidates") throw new Error("Recover Scans must not search");
    return baseApi(input, init);
  }));
  render(<AuthenticationGate><MediaDetail item={mediaItem} breadcrumbs={[]} onNavigate={vi.fn()} onDirtyChange={vi.fn()} /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  const button = await screen.findByRole("button", { name: "Recover Scans" });
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAccessibleDescription(/no local scans of this item's other versions or files/);
  expect(probes).toContainEqual(expect.objectContaining({ item_id: "movie-1", media_source_id: "source-1" }));
  expect(screen.queryByRole("button", { name: "Find Filters" })).not.toBeInTheDocument();
});
