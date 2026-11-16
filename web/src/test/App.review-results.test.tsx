import { act, cleanup, fireEvent, render as renderComponent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import type { ReviewFilter } from "../api";
import { testPublication } from "../review/export-test-fixtures";
import { emptyExports, mediaItem, json, installProductionApi, completeScan, savedReview, reviewFilter, openFirstReviewFindingGroup } from "./app-test-fixtures";

describe("production saved-review UI authority", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("clears pre-existing catalog review provenance when its sidecar is deleted", async () => {
    const scan = completeScan();
    const reviewExports = emptyExports();
    reviewExports.exports.edl = {
      format: "edl",
      present: true,
      exported_at: "2026-09-14T12:00:00Z",
      exported_scans: [{ id: scan.id, scan_id: scan.scan_id, name: scan.name, origin: scan.origin, role: "audio" }],
      review_id: "review-1",
      exported_format_revision: 1,
      current: true,
      outdated: false,
      exported_review: null,
    };
    const review = savedReview({
      exports: reviewExports,
      memberships: [{ scan_id: scan.id, role: "audio" }],
      scans: [{ scan, role: "audio" }],
      filters: [reviewFilter("audio-filter", "audio")],
    });
    const activeExports = { exports: { ...reviewExports.exports, edl: { ...reviewExports.exports.edl, exported_review: review } } };
    const activeItem = {
      ...mediaItem,
      exports: activeExports,
      versions: mediaItem.versions.map((version) => ({ ...version, exports: activeExports })),
    };
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/library-items/movie-1" && method === "GET") return json(activeItem);
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/library-items/movie-1/exports/edl" && method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      return baseApi(input, init);
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    const reviewRow = (await screen.findByRole("button", { name: "Open review Existing review" })).closest("tr")!;
    expect(reviewRow).toHaveTextContent("EDL · Current");

    fireEvent.click(screen.getByRole("button", { name: "Manage exports" }));
    const dialog = await screen.findByRole("dialog", { name: "Manage exports" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete EDL" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Delete EDL" }));

    await waitFor(() => expect(reviewRow).not.toHaveTextContent("EDL · Current"));
    expect(screen.queryByText(/EDL · Current/)).not.toBeInTheDocument();
  });

  it("synchronizes a catalog review's outdated, export, and delete lifecycle", async () => {
    const scan = completeScan();
    let review = savedReview({
      id: "review-outdated",
      media_item_id: "movie-media",
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
      name: "Movie review",
      memberships: [{ scan_id: scan.id, role: "audio" }],
      scans: [{ scan, role: "audio" }],
      filters: [reviewFilter("outdated-audio-filter", "audio")],
    });
    let status = emptyExports();
    status.exports.edl = {
      format: "edl",
      present: true,
      exported_at: "2026-09-14T11:00:00Z",
      exported_scans: [{ id: scan.id, scan_id: scan.scan_id, name: scan.name, origin: scan.origin, role: "audio" }],
      review_id: review.id,
      exported_format_revision: 1,
      current: false,
      outdated: true,
      exported_review: review,
    };
    const item = () => ({ ...mediaItem, exports: status, versions: mediaItem.versions.map((version) => ({ ...version, exports: status })) });
    const exportedItemIds: string[] = [];
    const exportReads: string[] = [];
    const discoveryReads: string[] = [];
    const deletedSources: string[] = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/library-items/movie-1" && method === "GET") return json(item());
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/scans/queue") return json([]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-outdated" && method === "GET") return json(review);
      if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") {
        exportReads.push(url.searchParams.get("media_source_id") || "");
        return json(status);
      }
      if (url.pathname === "/api/exports" && method === "GET") {
        discoveryReads.push(`${url.searchParams.get("media_item_id")}:${url.searchParams.get("media_source_id")}`);
        return json({ publications: status.publications ?? [] });
      }
      if (url.pathname === "/api/reviews/review-outdated/exports/edl" && method === "POST") {
        exportedItemIds.push(review.target.item_id);
        const reviewExports = { exports: { ...status.exports, edl: { ...status.exports.edl, exported_at: "2026-09-14T12:00:00Z", exported_format_revision: review.edl_content_revision, current: true, outdated: false, exported_review: null } } };
        review = { ...review, exports: reviewExports };
        const publication = testPublication({ media_item_id: "movie-media", review_id: review.id });
        status = { exports: { ...reviewExports.exports, edl: { ...reviewExports.exports.edl, exported_review: review } }, publications: [publication] };
        return json(publication);
      }
      if (url.pathname === "/api/library-items/movie-1/exports/edl" && method === "DELETE") {
        deletedSources.push(url.searchParams.get("media_source_id") || "");
        return new Response(null, { status: 204 });
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    const reviewRow = (await screen.findByRole("button", { name: "Open review Movie review" })).closest("tr")!;
    expect(reviewRow).toHaveTextContent("EDL · Outdated");

    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    const exportDialog = await screen.findByRole("dialog", { name: "Export" });
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Publish EDL" }));
    await waitFor(() => expect(reviewRow).toHaveTextContent("EDL · Current"));
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Close" }));
    expect(reviewRow).not.toHaveTextContent("Outdated");
    expect(exportedItemIds).toEqual(["movie-1"]);
    expect(exportReads.at(-1)).toBe("source-1");
    expect(discoveryReads.at(-1)).toBe("movie-media:source-1");
    expect(screen.getByRole("listitem", { name: "EDL · Personal · You" })).toHaveTextContent("CurrentFrom this review.");

    fireEvent.click(screen.getByRole("button", { name: "Manage exports" }));
    const dialog = await screen.findByRole("dialog", { name: "Manage exports" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete EDL" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Delete EDL" }));
    await waitFor(() => expect(deletedSources).toEqual(["source-1"]));
    await waitFor(() => {
      expect(screen.queryByText(/EDL · Current/)).not.toBeInTheDocument();
      expect(reviewRow).not.toHaveTextContent("EDL · Current");
    });
  });

  it("sorts the review list without replacing the active dirty draft", async () => {
    const recentReview = savedReview({
      id: "review-recent", name: "Recent review",
      created_at: "2026-09-14T11:00:00Z",
      updated_at: "2026-09-14T12:00:00Z",
    });
    const olderReview = {
      ...recentReview,
      id: "review-older",
      name: "Alphabetical review",
      created_at: "2026-09-14T09:00:00Z",
      updated_at: "2026-09-14T10:00:00Z",
    };
    const baseApi = installProductionApi();
    const reviewSortRequests: string[] = [];
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/library-items/movie-1/reviews") {
        reviewSortRequests.push(`${url.searchParams.get("sort")}:${url.searchParams.get("direction")}`);
        return json(url.searchParams.get("sort") === "name"
          ? [olderReview, recentReview]
          : [recentReview]);
      }
      if (url.pathname === "/api/reviews/review-recent") return json(recentReview);
      if (url.pathname === "/api/reviews/review-older") return json(olderReview);
      return baseApi(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Rename review" }));
    const reviewName = screen.getByLabelText("Review name", { exact: true });
    fireEvent.change(reviewName, { target: { value: "Unsaved review name" } });
    expect(screen.queryByLabelText("Review sort")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reverse review sort" })).not.toBeInTheDocument();
    const modifiedHeader = screen.getByRole("columnheader", { name: "Modified" });
    expect(modifiedHeader).toHaveAttribute("aria-sort", "descending");
    expect(modifiedHeader).toHaveTextContent("Modified ▼");
    const nameSort = screen.getByRole("button", { name: "Name" });
    fireEvent.click(nameSort);

    expect(await screen.findByText("Alphabetical review")).toBeVisible();
    await waitFor(() => expect(reviewSortRequests).toContain("name:asc"));
    const nameHeader = screen.getByRole("columnheader", { name: "Name" });
    expect(nameHeader).toHaveAttribute("aria-sort", "ascending");
    expect(nameHeader).toHaveTextContent("Name ▲");
    fireEvent.click(nameSort);
    await waitFor(() => expect(reviewSortRequests).toContain("name:desc"));
    expect(nameHeader).toHaveAttribute("aria-sort", "descending");
    expect(nameHeader).toHaveTextContent("Name ▼");
    expect(screen.getByLabelText("Review name", { exact: true })).toHaveValue("Unsaved review name");
  });

  it("presents and edits linked saved-review results from the local draft", async () => {
    const scan = {
      id: "scan-1", scan_id: 7, media_item_id: "media-1", jellyfin_item_id: "movie-1",
      library_id: "movies", item_name: "Example Movie", item_type: "Movie",
      media_source_id: "source-1", media_source_name: "4K HDR", status: "complete",
      scan_audio: true, audio_model: "base", audio_filter: "better_profanity",
      scan_video: true, video_model: "video", coarse_scan_interval: null,
      coarse_scan_enabled: false, qwen_model: "qwen3-vl-8b-instruct-4bit", qwen_summaries_per_segment: 3,
      qwen_segment_join_gap_seconds: 0, duration_seconds: 120, video_hash: null,
      error_message: null, finding_count: 3, manual: false, edited: false,
      scan_started_at: "2026-09-14T10:00:00Z", scan_duration_seconds: 30,
      created_at: "2026-09-14T10:00:00Z", updated_at: "2026-09-14T10:00:30Z",
      name: "Primary", origin: "imported", import_source_name: "input.edl", imported_at: "2026-09-14T10:00:00Z",
    };
    const review = {
      id: "review-rich", media_item_id: "movie-1",
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
      name: "Rich review", revision: 1, content_revision: 1,
      created_at: "2026-09-14T10:00:00Z", updated_at: "2026-09-14T10:00:00Z",
      memberships: [{ scan_id: "scan-1", role: "both" }],
      scans: [{ scan, role: "both" }],
      filters: [
        { id: "visual-1", source_scan_id: "scan-1", source_finding_id: 1, source: "visual", category: "violence", subcategory: "violence", severity: "medium", mcf_channel: "video", mcf_comment: null, label: "Fight", details: "Fight in hallway", group_key: "violence", start_seconds: 10, end_seconds: 20, confidence: 0.9, edit_action: "skip", selected: true },
        { id: "visual-2", source_scan_id: "scan-1", source_finding_id: 2, source: "visual", category: "violence", subcategory: "violence", severity: "high", mcf_channel: "video", mcf_comment: null, label: "Weapon", details: "Weapon visible", group_key: "violence", start_seconds: 15, end_seconds: 25, confidence: 0.8, edit_action: "skip", selected: false },
        { id: "visual-clean", source_scan_id: "scan-1", source_finding_id: 4, source: "visual", category: "violence", subcategory: "violence", severity: "low", mcf_channel: "video", mcf_comment: null, label: "Clean", details: "No objectionable content detected", group_key: "No objectionable content detected", start_seconds: 30, end_seconds: 35, confidence: 0.9, edit_action: "skip", selected: false },
        { id: "audio-1", source_scan_id: "scan-1", source_finding_id: 3, source: "audio", category: "language", subcategory: "swearing", severity: "low", mcf_channel: "audio", mcf_comment: null, label: "swearing", details: "A line with swearing", group_key: null, start_seconds: 40, end_seconds: 42, confidence: 0.95, edit_action: "mute", selected: true },
      ],
    };
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-rich") return json(review);
      if (url.pathname === "/api/settings/filters/apply") {
        const body = JSON.parse(String(init?.body)) as { filters: ReviewFilter[] };
        return json({
          filters: body.filters.map((filter) => ({ ...filter, selected: filter.source === "visual" })),
          selection_changes: 1,
          severity_changes: 0,
          channel_changes: 0,
        });
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    expect(await screen.findByText("1/2 results")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Review filters" })).not.toBeInTheDocument();
    expect(screen.getByText("Skipped Time 0:10")).toBeVisible();
    expect(screen.getByRole("img", { name: "1 selected results across 1 scans" })).toBeVisible();
    expect(screen.getAllByText(/Imported from input\.edl on/).length).toBeGreaterThan(0);
    expect(screen.queryByText("No objectionable content detected")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show No Objectionable Content" }));
    expect(await screen.findByText("1/3 results")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Hide No Objectionable Content" }));
    expect(await screen.findByText("1/2 results")).toBeVisible();

    const audioSection = screen.getByRole("region", { name: "Audio review filters" });
    const languageFamily = within(audioSection).getByRole("button", { name: /^Language family, 1 result, 1 selected$/i });
    expect(languageFamily).toHaveAttribute("aria-expanded", "true");
    expect(languageFamily).not.toHaveTextContent("Swearing");
    const swearingSubcategory = within(audioSection).getByRole("button", { name: /^Swearing subcategory, 1 result, 1 selected$/i });
    expect(swearingSubcategory).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(swearingSubcategory);
    const swearingGroup = within(audioSection).getByRole("button", { name: /^swear finding group, 1 result, 1 selected$/i });
    expect(swearingGroup).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(swearingGroup);
    const censoredDescriptions = within(audioSection).getAllByText("sw**r*ng");
    expect(censoredDescriptions).toHaveLength(1);
    expect(within(audioSection).queryByText("swearing")).not.toBeInTheDocument();
    fireEvent.focus(censoredDescriptions[0]);
    expect(within(audioSection).getByRole("tooltip")).toHaveTextContent("A line with swearing");
    fireEvent.blur(censoredDescriptions[0]);

    const videoSection = screen.getByRole("region", { name: "Video review filters" });
    expect(within(videoSection).getByRole("button", { name: /^Violence family, 1 result, 0 selected$/i })).toHaveAttribute("aria-expanded", "true");
    const violenceSubcategory = within(videoSection).getByRole("button", { name: /^Violence \(General\) subcategory, 1 result, 0 selected$/i });
    expect(violenceSubcategory).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(violenceSubcategory);
    const violenceGroup = within(videoSection).getByRole("button", { name: /^violence finding group, 1 result, 0 selected$/i });
    const violenceUse = within(videoSection).getByRole("checkbox", { name: "Use all video filters in violence finding group" });
    expect(violenceUse).toHaveAttribute("aria-checked", "mixed");
    expect(violenceUse).toHaveProperty("indeterminate", true);
    expect(violenceGroup).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(violenceUse);
    expect(await screen.findByText("2/2 results")).toBeVisible();
    expect(violenceGroup).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(violenceUse);
    expect(await screen.findByText("1/2 results")).toBeVisible();
    fireEvent.click(violenceGroup);
    const linkedResult = screen.getByRole("checkbox", { name: /Include Fight in hallway; Weapon visible at 0:10/ });
    expect(linkedResult).toHaveAttribute("aria-checked", "false");
    fireEvent.click(linkedResult);
    expect(await screen.findByText("2/2 results")).toBeVisible();

    const audioTimeHeader = within(audioSection).getByRole("columnheader", { name: "Time" });
    expect(audioTimeHeader).toHaveAttribute("aria-sort", "ascending");
    expect(audioTimeHeader).toHaveTextContent("Time ▲");
    expect(screen.queryByRole("group", { name: "Sort saved review results" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply Filter Defaults" }));
    expect(await screen.findByText("1/2 results")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("1 selection, 0 severity, and 0 channel changes");

    const previewButton = screen.getByRole("button", { name: "Preview filter visual-1" });
    expect(previewButton).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(previewButton).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Edit filter visual-1" }));
    fireEvent.change(screen.getByLabelText("Filter visual-1 label"), { target: { value: "Changed" } });
    expect(previewButton).toBeDisabled();
  });

  it("renders ordered MCF tiers with independent row sorting and synchronized linked instances", async () => {
    const scan = completeScan();
    const visual = (id: string, details: string, start: number, severity: ReviewFilter["severity"], overrides: Partial<ReviewFilter> = {}): ReviewFilter => ({
      ...reviewFilter(id, "visual"), details, label: details, start_seconds: start, end_seconds: start + 1,
      severity, group_key: "common", selected: false, ...overrides,
    });
    const audio = (id: string, details: string, start: number, severity: ReviewFilter["severity"]): ReviewFilter => ({
      ...reviewFilter(id, "audio"), label: id === "audio-alpha" ? "swearing" : "murder", details,
      start_seconds: start, end_seconds: start + 1, severity, selected: false,
    });
    const review = savedReview({
      id: "review-tiered",
      memberships: [{ scan_id: scan.id, role: "both" }],
      scans: [{ scan, role: "both" }],
      filters: [
        visual("visual-none", "None severity", 40, null),
        visual("visual-low", "Low severity", 30, "low"),
        visual("visual-medium", "Medium severity", 20, "medium"),
        visual("visual-high", "High severity", 10, "high"),
        visual("visual-linked-a", "Linked fight", 60, "medium", { group_key: "alpha", end_seconds: 70 }),
        visual("visual-linked-b", "Linked weapon", 65, "high", { subcategory: "weapons", group_key: "zulu", end_seconds: 75 }),
        visual("visual-legacy", "Legacy finding", 80, null, { category: null, subcategory: null, group_key: "legacy" }),
        audio("audio-alpha", "Alpha audio", 10, "low"),
        audio("audio-zulu", "Zulu audio", 30, "high"),
      ],
    });
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-tiered") return json(review);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    const videoSection = await screen.findByRole("region", { name: "Video review filters" });
    await within(videoSection).findByRole("button", { name: /Violence family/ });
    const familyNames = within(videoSection).getAllByRole("button", { name: /family,/ }).map((button) => button.getAttribute("aria-label")?.split(" family,")[0]);
    expect(familyNames).toEqual(["Unclassified", "Violence"]);
    const unclassifiedFamily = within(videoSection).getByRole("button", { name: /Unclassified family/ });
    expect(unclassifiedFamily).toHaveAttribute("aria-expanded", "true");
    const unclassifiedUse = within(videoSection).getByRole("checkbox", { name: "Use all video filters in Unclassified family" });
    fireEvent.click(unclassifiedUse);
    await waitFor(() => expect(within(screen.getByRole("region", { name: "Video review filters" })).getByRole("button", { name: /Unclassified family, 1 result, 1 selected/ })).toBeVisible());
    fireEvent.click(within(screen.getByRole("region", { name: "Video review filters" })).getByRole("checkbox", { name: "Use all video filters in Unclassified family" }));
    const unclassifiedSubcategory = within(screen.getByRole("region", { name: "Video review filters" })).getByRole("button", { name: /Unclassified subcategory/ });
    expect(unclassifiedSubcategory).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(unclassifiedSubcategory);
    const legacyGroup = within(screen.getByRole("region", { name: "Video review filters" })).getByRole("button", { name: /legacy finding group/ });
    fireEvent.click(legacyGroup);
    expect(within(screen.getByRole("region", { name: "Video review filters" })).getByRole("checkbox", { name: "Include Legacy finding at 1:20" })).toBeVisible();
    fireEvent.click(legacyGroup);

    const violenceGeneral = within(videoSection).getByRole("button", { name: /Violence \(General\) subcategory/ });
    const weapons = within(videoSection).getByRole("button", { name: /Weapons subcategory/ });
    expect(violenceGeneral.compareDocumentPosition(weapons)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    fireEvent.click(violenceGeneral);
    const alphaGroup = within(videoSection).getByRole("button", { name: /alpha finding group/ });
    const commonGroup = within(videoSection).getByRole("button", { name: /common finding group/ });
    expect(commonGroup.compareDocumentPosition(alphaGroup)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    fireEvent.click(within(videoSection).getByRole("button", { name: "Time" }));
    expect(alphaGroup.compareDocumentPosition(commonGroup)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    fireEvent.click(within(videoSection).getByRole("button", { name: "Description" }));
    expect(alphaGroup.compareDocumentPosition(commonGroup)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    fireEvent.click(within(videoSection).getByRole("button", { name: "Time" }));
    expect(commonGroup.compareDocumentPosition(alphaGroup)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(commonGroup).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(commonGroup);

    const rowNames = () => within(screen.getByRole("region", { name: "Video review filters" })).getAllByRole("checkbox", { name: /^Include / }).map((checkbox) => checkbox.getAttribute("aria-label"));
    expect(rowNames()).toEqual([
      "Include High severity at 0:10",
      "Include Medium severity at 0:20",
      "Include Low severity at 0:30",
      "Include None severity at 0:40",
    ]);
    const videoTimeHeader = within(videoSection).getByRole("columnheader", { name: "Time" });
    expect(videoTimeHeader).toHaveAttribute("aria-sort", "ascending");
    expect(videoTimeHeader).toHaveTextContent("Time ▲");
    fireEvent.click(within(videoSection).getByRole("button", { name: "Severity" }));
    expect(rowNames()).toEqual([
      "Include None severity at 0:40",
      "Include Low severity at 0:30",
      "Include Medium severity at 0:20",
      "Include High severity at 0:10",
    ]);
    fireEvent.click(within(videoSection).getByRole("button", { name: "Severity" }));
    expect(rowNames()).toEqual([
      "Include High severity at 0:10",
      "Include Medium severity at 0:20",
      "Include Low severity at 0:30",
      "Include None severity at 0:40",
    ]);
    expect(within(videoSection).getByRole("columnheader", { name: "Severity" })).toHaveTextContent("Severity ▼");

    const audioSection = screen.getByRole("region", { name: "Audio review filters" });
    expect(within(audioSection).getByRole("columnheader", { name: "Time" })).toHaveAttribute("aria-sort", "ascending");
    await openFirstReviewFindingGroup(audioSection);
    const murderGroup = within(audioSection).getByRole("button", { name: /murder finding group/ });
    expect(murderGroup).toBeVisible();
    expect(within(audioSection).queryByRole("button", { name: /murd finding group/ })).not.toBeInTheDocument();
    fireEvent.click(murderGroup);
    const audioRowNames = () => within(screen.getByRole("region", { name: "Audio review filters" })).getAllByRole("checkbox", { name: /^Include / }).map((checkbox) => checkbox.getAttribute("aria-label"));
    expect(audioRowNames()).toEqual(["Include swearing at 0:10", "Include murder at 0:30"]);
    fireEvent.click(within(screen.getByRole("region", { name: "Audio review filters" })).getByRole("button", { name: "Description" }));
    expect(audioRowNames()).toEqual(["Include murder at 0:30", "Include swearing at 0:10"]);
    fireEvent.click(within(screen.getByRole("region", { name: "Audio review filters" })).getByRole("button", { name: "Description" }));
    expect(audioRowNames()).toEqual(["Include swearing at 0:10", "Include murder at 0:30"]);
    expect(within(videoSection).getByRole("columnheader", { name: "Severity" })).toHaveAttribute("aria-sort", "descending");

    fireEvent.click(alphaGroup);
    fireEvent.click(weapons);
    const zuluGroup = within(videoSection).getByRole("button", { name: /zulu finding group/ });
    fireEvent.click(zuluGroup);
    const linkedRows = within(videoSection).getAllByRole("checkbox", { name: /^Include Linked fight; Linked weapon at 1:00/ });
    expect(linkedRows).toHaveLength(2);
    expect(within(videoSection).getAllByRole("img", { name: "Linked result repeated in another category" })).toHaveLength(4);
    fireEvent.click(linkedRows[0]);
    await waitFor(() => within(screen.getByRole("region", { name: "Video review filters" })).getAllByRole("checkbox", { name: /^Include Linked fight; Linked weapon at 1:00/ }).forEach((checkbox) => expect(checkbox).toBeChecked()));
  });

  it("does not let a late initial review load replace an imported review", async () => {
    const initialReview = {
      id: "review-initial", media_item_id: "movie-1",
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
      name: "Initial review", revision: 0, content_revision: 0,
      created_at: "2026-09-14T10:00:00Z", updated_at: "2026-09-14T10:00:00Z",
      memberships: [], scans: [], filters: [],
    };
    const importedReview = { ...initialReview, id: "review-imported", name: "Imported review" };
    let resolveInitial: (response: Response) => void = () => undefined;
    const pendingInitial = new Promise<Response>((resolve) => { resolveInitial = resolve; });
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([initialReview]);
      if (url.pathname === "/api/reviews/review-initial") return pendingInitial;
      if (url.pathname === "/api/edl-imports/upload") return json({
        review: importedReview,
        scan: { id: "scan-imported", scan_id: 3, name: "Imported review", origin: "imported" },
        warning: null,
      }, 201);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    const importDialog = screen.getByRole("dialog", { name: "Import" });
    fireEvent.change(within(importDialog).getByLabelText("Import review name"), { target: { value: "Imported review" } });
    fireEvent.change(within(importDialog).getByLabelText("EDL file"), { target: { files: [new File(["1 2 1"], "import.edl")] } });
    fireEvent.click(within(importDialog).getByRole("button", { name: "Import EDL" }));
    expect(await screen.findByRole("heading", { name: "Imported review", level: 3 })).toBeVisible();
    expect(screen.queryByLabelText("Review name", { exact: true })).not.toBeInTheDocument();

    await act(async () => { resolveInitial(json(initialReview)); });
    expect(screen.getByRole("heading", { name: "Imported review", level: 3 })).toBeVisible();
  });

  it("keeps an imported review when the initial empty review list arrives late", async () => {
    const importedReview = savedReview({ id: "review-imported", name: "Imported review" });
    let resolveInitialList: (response: Response) => void = () => undefined;
    const pendingInitialList = new Promise<Response>((resolve) => { resolveInitialList = resolve; });
    let listRequests = 0;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/library-items/movie-1/reviews") {
        listRequests += 1;
        return listRequests === 2 ? pendingInitialList : json([]);
      }
      if (url.pathname === "/api/edl-imports/upload") {
        return json({ review: importedReview, scan: completeScan(), warning: null }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await waitFor(() => expect(listRequests).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    const importDialog = screen.getByRole("dialog", { name: "Import" });
    fireEvent.change(within(importDialog).getByLabelText("Import review name"), { target: { value: "Imported review" } });
    fireEvent.change(within(importDialog).getByLabelText("EDL file"), { target: { files: [new File(["1 2 1"], "import.edl")] } });
    fireEvent.click(within(importDialog).getByRole("button", { name: "Import EDL" }));
    expect(await screen.findByRole("heading", { name: "Imported review", level: 3 })).toBeVisible();

    await act(async () => { resolveInitialList(json([])); });
    expect(screen.getByRole("heading", { name: "Imported review", level: 3 })).toBeVisible();
  });
});
