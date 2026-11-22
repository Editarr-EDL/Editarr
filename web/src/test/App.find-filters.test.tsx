import { openWorkflowStep } from "./review-workflow-test-helpers";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MediaDetail } from "../catalog/media-detail";
import { AuthenticationGate } from "../auth/auth-gate";
import { completeScan, installProductionApi, json, mediaItem, savedReview } from "./app-test-fixtures";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("uses the shared local flow for Jellyfin and the selected review version", async () => {
  const scan = completeScan({ id: "found-scan", name: "Found local evidence", media_source_id: "source-2", library_id: "movies" });
  const review = savedReview();
  const baseApi = installProductionApi();
  const searches: unknown[] = [];
  const associations: unknown[] = [];
  const historyPaths: (string | null)[] = [];
  let associated = false;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
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
  fireEvent.click(screen.getByRole("button", { name: "Find Filters" }));
  const dialog = await screen.findByRole("dialog", { name: "Found Filters" });
  expect(searches).toEqual([{
    source: "jellyfin", item_id: "movie-1", library_id: "movies", media_source_id: "source-2",
    item_name: "Example Movie", item_type: "Movie", duration_seconds: 140,
    scene_name: "Alternate.Scene", release_group: "OTHER", media_format: "MP4", media_provider_id: null,
  }]);
  expect(within(dialog).getByRole("checkbox", { name: /Associate Found local evidence/ })).toBeChecked();
  fireEvent.click(within(dialog).getByRole("button", { name: "Associate Selected" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Found Filters" })).not.toBeInTheDocument());
  expect(associations).toEqual([{ media_item_id: "resolved-media", scan_ids: [scan.id] }]);
  expect(await screen.findByRole("status")).toHaveTextContent("Associated 1 scan.");
  expect(await within(screen.getByRole("list", { name: "Scan history" })).findByText(/Found local evidence/)).toBeVisible();
  expect(historyPaths.at(-1)).toBeNull();
  await openWorkflowStep("Review");
  expect(screen.getByRole("button", { name: "Open review Existing review" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "New Review" }));
  fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Reuse found evidence" } });
  fireEvent.click(screen.getByRole("button", { name: "Start new review" }));
  expect(await screen.findByRole("checkbox", { name: /Found local evidence/ })).toBeVisible();
});

it("uses item metadata and can search when the catalog withholds all paths", async () => {
  const baseApi = installProductionApi();
  const searches: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    if (String(input).endsWith("/filter-candidates")) {
      searches.push(JSON.parse(String(init?.body)));
      return json({ media_item_id: "media-1", matches: [] });
    }
    return baseApi(input, init);
  }));
  const props = { breadcrumbs: [], onNavigate: vi.fn(), onDirtyChange: vi.fn() };
  const { rerender } = render(<AuthenticationGate><MediaDetail {...props} item={{ ...mediaItem, versions: [] }} /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  fireEvent.click(await screen.findByRole("button", { name: "Find Filters" }));
  const dialog = await screen.findByRole("dialog", { name: "Found Filters" });
  expect(searches).toEqual([{
    source: "jellyfin", item_id: "movie-1", library_id: "movies", media_source_id: null,
    item_name: "Example Movie", item_type: "Movie", duration_seconds: 120,
    scene_name: null, release_group: null, media_format: "MKV", media_provider_id: null,
  }]);
  expect(within(dialog).getByText("No matching filter scans were found.")).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "Associate Selected" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  rerender(<AuthenticationGate><MediaDetail {...props} item={{ ...mediaItem, path: null, versions: [] }} /></AuthenticationGate>);
  fireEvent.click(screen.getByRole("button", { name: "Find Filters" }));
  await screen.findByRole("dialog", { name: "Found Filters" });
  expect(searches).toHaveLength(2);
  expect(searches.every((search) => !Object.prototype.hasOwnProperty.call(search, "source_path"))).toBe(true);
});