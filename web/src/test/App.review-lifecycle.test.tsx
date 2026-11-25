import { exportAction, findFilterAction, openReviewSources, openWorkflowStep, queryFilterAction, revealMediaFiles, useCategorySort } from "./review-workflow-test-helpers";
import { act, cleanup, fireEvent, render as renderComponent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import type { ExportPublication, ExportStatus, SavedReview } from "../api";
import { testPublication } from "../review/export-test-fixtures";
import { emptyExports, mediaItem, json, installProductionApi, completeScan, savedReview, reviewFilter, openFirstReviewFindingGroup } from "./app-test-fixtures";

describe("production saved-review UI authority", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("lists every saved-review and active export dependency before scan deletion", async () => {
    const scan = completeScan();
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/scans/scan-combined/references") return json({
        scan_id: scan.id,
        active_exports: ["edl", "mcf"],
        reviews: [
          { id: "review-a", name: "Shared name", updated_at: "2026-09-14T10:00:00Z" },
          { id: "review-b", name: "Shared name", updated_at: "2026-09-14T11:00:00Z" },
        ],
      });
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(await screen.findByRole("button", { name: "Delete Combined scan · Scan #7" }));

    const dialog = await screen.findByRole("dialog", { name: "Delete Combined scan · Scan #7?" });
    expect(within(dialog).getByText("Active EDL export")).toBeVisible();
    expect(within(dialog).getByText("Active MCF export")).toBeVisible();
    expect(within(dialog).getAllByText(/Shared name · modified .* · ID review-/)).toHaveLength(2);
    expect(within(dialog).getByRole("button", { name: "Delete scan" })).toBeDisabled();
  });

  it("uses role-local snapshots and refetches a role after a saved removal", async () => {
    const scan = completeScan();
    const audioFilter = reviewFilter("audio-filter", "audio");
    const videoFilter = reviewFilter("video-filter", "visual");
    let review = savedReview({
      memberships: [{ scan_id: scan.id, role: "both" }],
      scans: [{ scan, role: "both" }],
      filters: [audioFilter, videoFilter],
    });
    let findingRequests = 0;
    const saves: Array<Pick<SavedReview, "memberships" | "filters">> = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/scans/scan-combined/findings") {
        findingRequests += 1;
        return json({ revision: 1, findings: [
          { ...audioFilter, id: 11, scan_id: scan.id },
          { ...videoFilter, id: 12, scan_id: scan.id },
        ] });
      }
      if (url.pathname === "/api/reviews/review-1" && method === "PUT") {
        const payload = JSON.parse(String(init?.body)) as Pick<SavedReview, "name" | "memberships" | "filters">;
        saves.push(payload);
        review = {
          ...review,
          name: payload.name,
          revision: Number(review.revision) + 1,
          content_revision: Number(review.content_revision) + 1,
          memberships: payload.memberships,
          filters: payload.filters,
        };
        return json(review);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    // The role is chosen per combined scan in the Sources popover, which opening a row's menu closes.
    const role = async () => within(await openReviewSources()).findByLabelText("Review role for Combined scan · Scan #7");
    expect(await role()).toHaveValue("both");
    expect(screen.getByRole("table", { name: "Review filters by time" })).toBeVisible();
    await useCategorySort();
    await openFirstReviewFindingGroup(await screen.findByRole("region", { name: "Audio review filters" }));
    await openFirstReviewFindingGroup(screen.getByRole("region", { name: "Video review filters" }));
    expect(screen.queryByRole("group", { name: "Audio filter" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Video filter" })).not.toBeInTheDocument();
    for (const menu of screen.getAllByRole("button", { name: /^More actions for / })) expect(menu.closest("td")).not.toBeNull();
    expect(await findFilterAction("Delete filter audio-filter")).toBeEnabled();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(await findFilterAction("Delete filter video-filter")).toBeEnabled();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.click(await findFilterAction("Edit filter audio-filter"));
    expect(screen.getByRole("group", { name: "Audio filter" })).toBeVisible();
    fireEvent.click(await findFilterAction("Edit filter video-filter"));
    expect(screen.queryByRole("group", { name: "Audio filter" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Video filter" })).toBeVisible();

    fireEvent.change(await role(), { target: { value: "audio" } });
    expect(await findFilterAction("Edit filter audio-filter")).toBeVisible();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(queryFilterAction("Edit filter video-filter")).toBeNull();
    fireEvent.change(await role(), { target: { value: "both" } });
    expect(await findFilterAction("Edit filter video-filter")).toBeVisible();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(findingRequests).toBe(0);

    fireEvent.change(await role(), { target: { value: "audio" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].memberships).toEqual([{ scan_id: scan.id, role: "audio" }]);
    expect(saves[0].filters.map((filter) => filter.source)).toEqual(["audio"]);

    fireEvent.change(await role(), { target: { value: "both" } });
    await waitFor(() => expect(findingRequests).toBe(1));
    await useCategorySort();
    const refreshedVideoSection = await screen.findByRole("region", { name: "Video review filters" });
    if (!within(refreshedVideoSection).queryByRole("button", { name: /^More actions for / })) {
      await openFirstReviewFindingGroup(refreshedVideoSection);
    }
    fireEvent.click(await findFilterAction(/^Edit filter /, refreshedVideoSection));
    expect(await screen.findByRole("group", { name: "Video filter" })).toBeVisible();
    fireEvent.change(await role(), { target: { value: "video" } });
    expect(screen.queryByRole("group", { name: "Audio filter" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(saves).toHaveLength(2));
    expect(saves[1].memberships).toEqual([{ scan_id: scan.id, role: "video" }]);

    fireEvent.change(await role(), { target: { value: "both" } });
    await waitFor(() => expect(findingRequests).toBe(2));
    const refreshedAudioSection = await screen.findByRole("region", { name: "Audio review filters" });
    if (!within(refreshedAudioSection).queryByRole("button", { name: /^More actions for / })) {
      await openFirstReviewFindingGroup(refreshedAudioSection);
    }
    fireEvent.click(await findFilterAction(/^Edit filter /, refreshedAudioSection));
    expect(await screen.findByRole("group", { name: "Audio filter" })).toBeVisible();
  });

  it("requires an explicit MCF metadata source after a create conflict", async () => {
    const firstScan = completeScan({ id: "scan-a", scan_id: 1, name: "Imported A", origin: "imported", mcf_document: { ...savedReview().mcf_document, source_version: "1.1.0", title: "Variant A", material_start_seconds: 0, material_end_seconds: 120 } });
    const secondScan = completeScan({ id: "scan-b", scan_id: 2, name: "Imported B", origin: "imported", mcf_document: { ...savedReview().mcf_document, source_version: "1.1.0", title: "Variant B", material_start_seconds: 0, material_end_seconds: 120 } });
    const requests: Array<Record<string, unknown>> = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([firstScan, secondScan]);
      if (url.pathname === "/api/library-items/movie-1/reviews" && method === "GET") return json([]);
      if (url.pathname === "/api/scans/scan-a/findings" || url.pathname === "/api/scans/scan-b/findings") return json({ revision: 0, findings: [] });
      if (url.pathname === "/api/library-items/movie-1/reviews" && method === "POST") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        requests.push(body);
        if (requests.length === 1) return json({ detail: {
          code: "mcf_metadata_source_required", message: "Choose source", line_number: null, references: null, filter_ids: [], document_fields: [],
          metadata_candidates: [
            { kind: "scan", scan_id: "scan-a", label: "Imported A", mcf_document: firstScan.mcf_document },
            { kind: "catalog", scan_id: null, label: "Catalog defaults", mcf_document: savedReview().mcf_document },
          ],
        } }, 409);
        return json(savedReview({ name: "Variant review", memberships: body.memberships as SavedReview["memberships"], scans: [], mcf_document: firstScan.mcf_document! }));
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "New Review" }));
    fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Variant review" } });
    fireEvent.click(screen.getByRole("button", { name: "Start new review" }));
    fireEvent.click(screen.getByRole("button", { name: "Save Review" }));

    expect(await screen.findByRole("group", { name: "Choose MCF metadata source" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Save Review" })).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Catalog defaults/));
    expect(screen.getByRole("button", { name: "Save Review" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save Review" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].mcf_metadata_seed).toEqual({ kind: "catalog" });
  });

  it("populates untitled and named review MCF metadata from the selected library item", async () => {
    const episodeItem = { ...mediaItem, item_type: "Episode", name: "Example Episode", season_number: 2, index_number: 4 };
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/libraries/movies/items") return json({ items: [episodeItem], total_count: 1, rating_values: [], resolution_values: [] });
      if (url.pathname === "/api/library-items/movie-1" && (!init?.method || init.method === "GET")) return json(episodeItem);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Episode" }));
    expect(await screen.findByLabelText("MCF title")).toHaveValue("Example Episode");
    expect(screen.getByLabelText("MCF year")).toHaveValue(2025);
    expect(screen.getByLabelText("MCF type")).toHaveValue("episode");
    expect(screen.getByLabelText("MCF season")).toHaveValue(2);
    expect(screen.getByLabelText("MCF episode")).toHaveValue(4);
    expect(screen.getByLabelText("MCF release")).toHaveValue("4K HDR");
    expect(screen.getByLabelText("MCF material start")).toHaveValue("00:00:00.000");
    expect(screen.getByLabelText("MCF material end")).toHaveValue("00:02:00.000");

    fireEvent.click(await screen.findByRole("button", { name: "New Review" }));
    fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Catalog review" } });
    fireEvent.click(screen.getByRole("button", { name: "Start new review" }));

    expect(screen.getByLabelText("MCF title")).toHaveValue("Example Episode");
    expect(screen.getByLabelText("MCF year")).toHaveValue(2025);
    expect(screen.getByLabelText("MCF type")).toHaveValue("episode");
    expect(screen.getByLabelText("MCF season")).toHaveValue(2);
    expect(screen.getByLabelText("MCF episode")).toHaveValue(4);
    expect(screen.getByLabelText("MCF release")).toHaveValue("4K HDR");
    expect(screen.getByLabelText("MCF material start")).toHaveValue("00:00:00.000");
    expect(screen.getByLabelText("MCF material end")).toHaveValue("00:02:00.000");
  });

  it("guards dirty duplication, allows duplicate names, and preserves edits after a nested stale conflict", async () => {
    const scan = completeScan();
    const sourceFilter = reviewFilter("audio-filter", "audio");
    const original = savedReview({
      memberships: [{ scan_id: scan.id, role: "audio" }],
      scans: [{ scan, role: "audio" }],
      filters: [sourceFilter],
    });
    let reviews = [original];
    const duplicateNames: string[] = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json(reviews);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(original);
      if (url.pathname === "/api/reviews/review-1/duplicate" && method === "POST") {
        const payload = JSON.parse(String(init?.body)) as { name: string };
        duplicateNames.push(payload.name);
        const duplicate = savedReview({
          id: "review-copy",
          name: payload.name,
          memberships: original.memberships,
          scans: original.scans,
          filters: [{ ...sourceFilter, id: "audio-filter-copy" }],
        });
        reviews = [duplicate, original];
        return json(duplicate, 201);
      }
      if (url.pathname === "/api/reviews/review-copy" && method === "GET") return json({ ...reviews[0], revision: 2 });
      if (url.pathname === "/api/reviews/review-copy" && method === "PUT") {
        return json({
          detail: {
            code: "stale_review_revision",
            message: "Review was changed elsewhere. Reload it before saving.",
            references: [{ review_id: "review-copy", revision: 2 }],
          },
        }, 409);
      }
      return baseApi(input, init);
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await useCategorySort();
    await openFirstReviewFindingGroup(await screen.findByRole("region", { name: "Audio review filters" }));
    fireEvent.click(await findFilterAction("Edit filter audio-filter"));
    fireEvent.change(await screen.findByLabelText("Filter audio-filter label"), { target: { value: "Dirty local edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    fireEvent.change(screen.getByLabelText("Duplicate review name"), { target: { value: "Existing review" } });
    fireEvent.click(within(screen.getByRole("dialog", { name: "Duplicate Review" })).getByRole("button", { name: "Duplicate" }));
    expect(confirm).toHaveBeenCalledWith("Discard unsaved changes and duplicate the last saved version of this review?");
    expect(duplicateNames).toEqual([]);

    fireEvent.click(within(screen.getByRole("dialog", { name: "Duplicate Review" })).getByRole("button", { name: "Duplicate" }));
    await waitFor(() => expect(duplicateNames).toEqual(["Existing review"]));
    expect(within(screen.getByRole("table", { name: "Saved reviews" })).getAllByRole("button", { name: /^Open review / })).toHaveLength(2);

    fireEvent.click(await findFilterAction("Edit filter audio-filter-copy"));
    const copiedLabel = await screen.findByLabelText("Filter audio-filter-copy label");
    fireEvent.change(copiedLabel, { target: { value: "Unsaved after conflict" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByRole("alert", { name: "Review conflict" })).toHaveTextContent("Review changed elsewhere");
    expect(copiedLabel).toHaveValue("Unsaved after conflict");
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "Save retained draft with latest policy" })).toBeEnabled();
  });

  it("requests real preview endpoints and revokes stale links when the draft changes", async () => {
    const scan = completeScan();
    const audioFilter = reviewFilter("audio-filter", "audio");
    const videoFilter = reviewFilter("video-filter", "visual");
    const review = savedReview({
      memberships: [{ scan_id: scan.id, role: "both" }],
      scans: [{ scan, role: "both" }],
      filters: [audioFilter, videoFilter],
    });
    const previewPaths: string[] = [];
    const createObjectUrl = vi.spyOn(URL, "createObjectURL")
      .mockReturnValueOnce("blob:audio-preview")
      .mockReturnValueOnce("blob:video-preview");
    const revokeObjectUrl = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1") return json(review);
      if (url.pathname.endsWith("/mute-preview") || url.pathname.endsWith("/video-preview")) {
        previewPaths.push(url.pathname);
        return new Response(new Blob(["preview"]), { headers: { "Content-Type": "application/octet-stream" } });
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    expect(screen.queryByRole("columnheader", { name: "Action" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Preview filter", { exact: true })).not.toBeInTheDocument();
    await useCategorySort();
    await openFirstReviewFindingGroup(await screen.findByRole("region", { name: "Audio review filters" }));
    const audioPreview = screen.getByRole("button", { name: "Preview filter audio-filter" });
    expect(audioPreview.closest("td")).not.toBeNull();
    fireEvent.click(audioPreview);
    const audioPlayer = await screen.findByLabelText("Mute preview for Spoken word at 0:05");
    expect(audioPlayer).toHaveAttribute("controls");
    expect(audioPlayer).toHaveAttribute("src", "blob:audio-preview");
    expect(previewPaths).toEqual(["/api/reviews/review-1/filters/audio-filter/mute-preview"]);

    const currentHeader = screen.getByRole("heading", { name: "Existing review", level: 3 }).closest(".saved-review-current-header") as HTMLElement;
    expect(within(currentHeader).getByRole("button", { name: "Duplicate" })).toBeVisible();
    expect(within(currentHeader).getByRole("button", { name: "Delete review" })).toBeVisible();
    fireEvent.click(within(currentHeader).getByRole("button", { name: "Rename review" }));
    fireEvent.change(screen.getByLabelText("Review name", { exact: true }), { target: { value: "Dirty review" } });
    await waitFor(() => expect(screen.queryByLabelText("Mute preview for Spoken word at 0:05")).not.toBeInTheDocument());
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:audio-preview");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await openFirstReviewFindingGroup(screen.getByRole("region", { name: "Video review filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview filter video-filter" }));
    const videoPlayer = await screen.findByLabelText("Video preview for Fight in hallway at 0:20");
    expect(videoPlayer).toHaveAttribute("controls");
    expect(videoPlayer).toHaveAttribute("src", "blob:video-preview");
    expect(previewPaths).toEqual([
      "/api/reviews/review-1/filters/audio-filter/mute-preview",
      "/api/reviews/review-1/filters/video-filter/video-preview",
    ]);
    expect(createObjectUrl).toHaveBeenCalledTimes(2);
  });

  it("keeps a newly opened review when the reread after publishing the previous one arrives late", async () => {
    const scan = completeScan();
    const contents = { memberships: [{ scan_id: scan.id, role: "audio" as const }], scans: [{ scan, role: "audio" as const }], filters: [reviewFilter("audio-filter", "audio")] };
    const first = savedReview(contents);
    const second = savedReview({ ...contents, id: "review-2", name: "Other review", updated_at: "2026-09-14T09:00:00Z" });
    let published = false;
    let releaseReread: (() => void) | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([first, second]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") {
        if (!published) return json(first);
        await new Promise<void>((resolve) => { releaseReread = resolve; });
        return json(first);
      }
      if (url.pathname === "/api/reviews/review-2" && method === "GET") return json(second);
      if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") return json({ ...emptyExports(), publications: [] });
      if (url.pathname === "/api/reviews/review-1/exports/edl" && method === "POST") {
        published = true;
        return json(testPublication({ review_id: first.id }));
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await screen.findByRole("heading", { name: "Existing review", level: 3 });
    fireEvent.click(await exportAction("EDL"));
    const exportDialog = await screen.findByRole("dialog", { name: "Export" });
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Publish EDL" }));
    await waitFor(() => expect(releaseReread).not.toBeNull());
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Close" }));
    fireEvent.change(screen.getByLabelText("Saved review"), { target: { value: second.id } });
    await waitFor(() => expect(screen.getByLabelText("Saved review")).toHaveValue(second.id));
    await openWorkflowStep("Review");
    await screen.findByRole("heading", { name: "Other review", level: 3 });
    await act(async () => { releaseReread!(); });
    expect(screen.getByRole("heading", { name: "Other review", level: 3 })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Existing review", level: 3 })).not.toBeInTheDocument();
  });

  it("synchronizes catalog export, outdated, review deletion with a kept file, and sidecar deletion", async () => {
    const scan = completeScan();
    let review = savedReview({
      memberships: [{ scan_id: scan.id, role: "audio" }],
      scans: [{ scan, role: "audio" }],
      filters: [reviewFilter("audio-filter", "audio")],
    });
    let reviewDeleted = false;
    let sidecarDeletes = 0;
    let status = emptyExports();
    const publications: ExportPublication[] = [];
    const exportRequests: Record<string, unknown>[] = [];
    const deletionRequests: Record<string, unknown>[] = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json(reviewDeleted ? [] : [review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") return json({ ...status, publications });
      if (url.pathname === "/api/exports" && method === "GET") return json({ publications });
      if (url.pathname === "/api/reviews/review-1/exports/edl" && method === "POST") {
        exportRequests.push(JSON.parse(String(init?.body)));
        publications.splice(0, publications.length, testPublication({ review_id: review.id, exported_format_revision: review.edl_content_revision }));
        status = emptyExports();
        status.exports.edl = { format: "edl", present: true, exported_at: "2026-09-14T12:00:00Z", review_id: review.id, exported_format_revision: review.edl_content_revision, current: true, outdated: false, exported_scans: [{ id: scan.id, scan_id: scan.scan_id, name: scan.name, origin: scan.origin, role: "audio" }], exported_review: null };
        review = { ...review, exports: status };
        return json(publications[0]);
      }
      if (url.pathname === "/api/reviews/review-1" && method === "PUT") {
        const payload = JSON.parse(String(init?.body)) as Pick<SavedReview, "name" | "memberships" | "filters">;
        review = {
          ...review,
          ...payload,
          revision: Number(review.revision) + 1,
          content_revision: Number(review.content_revision) + 1,
          edl_content_revision: Number(review.edl_content_revision) + 1,
          exports: { exports: { ...review.exports.exports, edl: { ...review.exports.exports.edl, current: false, outdated: true } } },
        };
        publications[0] = { ...publications[0], current: false, outdated: true };
        status = { exports: { ...status.exports, edl: { ...status.exports.edl, current: false, outdated: true } } };
        return json(review);
      }
      if (url.pathname === "/api/reviews/review-1/deletion" && method === "GET") return json({
        review_id: review.id, revision: review.revision, confirmation: "c".repeat(64),
        files: [{ publication: publications[0], choices: ["retain", "remove"] }], legacy_formats: [], scan_ids: [scan.id],
      });
      if (url.pathname === "/api/reviews/review-1/deletion" && method === "POST") {
        deletionRequests.push(JSON.parse(String(init?.body)));
        reviewDeleted = true;
        publications[0] = { ...publications[0], review_id: null, exported_format_revision: null };
        status = { exports: { ...status.exports, edl: { ...status.exports.edl, review_id: null, exported_review: null } } };
        return json({ review_id: review.id, retained: [publications[0].id], removed: [] });
      }
      if (url.pathname === "/api/library-items/movie-1/exports/edl" && method === "DELETE") {
        sidecarDeletes += 1;
        return new Response(null, { status: 204 });
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    const exportButton = await exportAction("EDL");
    exportButton.focus();
    fireEvent.click(exportButton);
    let exportDialog = await screen.findByRole("dialog", { name: "Export" });
    fireEvent.keyDown(exportDialog, { key: "Escape" });
    await waitFor(() => expect(exportButton).toHaveFocus());
    fireEvent.click(exportButton);
    exportDialog = await screen.findByRole("dialog", { name: "Export" });
    expect(within(exportDialog).getByRole("radio", { name: "Your personal files" })).toBeChecked();
    expect(within(exportDialog).getByRole("listitem", { name: "EDL at Your personal files" })).toHaveTextContent("No file published here yet.");
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Publish EDL" }));
    await waitFor(() => expect(within(exportDialog).getByRole("listitem", { name: "EDL at Your personal files" })).toHaveTextContent("CurrentFrom this review."));
    expect(exportRequests).toEqual([{ revision: 1, policy_owner_id: review.policy_owner_id, policy_revision: review.policy_revision, target: "self" }]);
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Close" }));
    await openWorkflowStep("Review");
    const reviewRow = screen.getByRole("button", { name: "Open review Existing review" }).closest("tr")!;
    await waitFor(() => expect(reviewRow).toHaveTextContent("EDL · Current"));
    expect(screen.getByRole("listitem", { name: "EDL · Personal · You" })).toHaveTextContent("Current");
    revealMediaFiles();
    expect(screen.getByRole("button", { name: "Manage legacy exports" })).toBeEnabled();

    await useCategorySort();
    await openFirstReviewFindingGroup(await screen.findByRole("region", { name: "Audio review filters" }));
    fireEvent.click(await findFilterAction("Edit filter audio-filter"));
    fireEvent.change(screen.getByLabelText("Filter audio-filter label"), { target: { value: "Changed after export" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(reviewRow).toHaveTextContent("EDL · Outdated"));
    await waitFor(() => expect(screen.getByRole("listitem", { name: "EDL · Personal · You" })).toHaveTextContent("Outdated"));

    fireEvent.click(screen.getByRole("button", { name: "Delete review" }));
    const deletion = await screen.findByRole("dialog", { name: "Delete review" });
    const file = await within(deletion).findByRole("group", { name: "EDL · Personal · You" });
    expect(within(file).getByRole("radio", { name: "Keep file" })).toBeChecked();
    fireEvent.click(within(deletion).getByRole("button", { name: "Delete review" }));
    await waitFor(() => expect(reviewRow).not.toBeInTheDocument());
    expect(deletionRequests).toEqual([{ revision: 2, confirmation: "c".repeat(64), files: [{ export_id: 41, choice: "retain" }] }]);
    expect(await screen.findByText("Review deleted. Published files kept: 1. Removed: 0.")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("listitem", { name: "EDL · Personal · You" })).toHaveTextContent("From a deleted or unavailable review."));
    expect(screen.getAllByText(/EDL · Outdated/).length).toBeGreaterThan(0);

    revealMediaFiles();
    fireEvent.click(screen.getByRole("button", { name: "Manage legacy exports" }));
    const dialog = await screen.findByRole("dialog", { name: "Manage exports" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete EDL" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Delete EDL" }));
    await waitFor(() => expect(sidecarDeletes).toBe(1));
    expect(screen.queryByText(/EDL · Current/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Manage legacy exports" })).not.toBeInTheDocument();
  });

  it("blocks MCF export on actionable preflight issues then preserves an active EDL", async () => {
    const scan = completeScan();
    const exports = emptyExports();
    exports.exports.edl = { format: "edl", present: true, exported_at: "2026-09-14T11:00:00Z", review_id: "review-1", exported_format_revision: 1, current: true, outdated: false, exported_scans: [], exported_review: null };
    let review = savedReview({ exports, memberships: [{ scan_id: scan.id, role: "audio" }], scans: [{ scan, role: "audio" }], filters: [reviewFilter("audio-filter", "audio")] });
    exports.exports.edl.exported_review = { ...review, exports: emptyExports() };
    let eligible = false;
    let mcfExports = 0;
    let versionStatus: ExportStatus = { ...exports, publications: [testPublication()] };
    let mcfDeletes = 0;
    const activeItem = { ...mediaItem, exports, versions: mediaItem.versions.map((version) => ({ ...version, exports })) };
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/library-items/movie-1" && method === "GET") return json(activeItem);
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/reviews/review-1/mcf/preflight") return json(eligible
        ? { review_id: review.id, revision: review.revision, eligible: true, issues: [] }
        : { review_id: review.id, revision: review.revision, eligible: false, issues: [{ code: "missing_severity", field: "severity", filter_id: "audio-filter", filter_position: 1, message: "Choose a severity for filter 1." }] });
      if (url.pathname === "/api/library-items/movie-1/exports" && method === "GET") return json(versionStatus);
      if (url.pathname === "/api/exports" && method === "GET") return json({ publications: versionStatus.publications });
      if (url.pathname === "/api/reviews/review-1/exports/mcf" && method === "POST") {
        mcfExports += 1;
        const nextExports = { exports: { ...exports.exports, mcf: { format: "mcf" as const, present: true, exported_at: "2026-09-14T12:00:00Z", review_id: review.id, exported_format_revision: 1, current: true, outdated: false, exported_scans: [], exported_review: review } } };
        review = { ...review, exports: nextExports };
        const publication = testPublication({ id: 42, format: "mcf" });
        versionStatus = { ...nextExports, publications: [testPublication(), publication] };
        return json(publication);
      }
      if (url.pathname === "/api/library-items/movie-1/exports/mcf" && method === "DELETE") {
        mcfDeletes += 1;
        return new Response(null, { status: 204 });
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await exportAction("MCF"));
    fireEvent.click(within(await screen.findByRole("dialog", { name: "Export" })).getByRole("button", { name: "Publish MCF" }));
    const issues = await screen.findByRole("alert", { name: "MCF export needs attention" });
    expect(screen.queryByRole("dialog", { name: "Export" })).not.toBeInTheDocument();
    fireEvent.click(within(issues).getByRole("button", { name: "Choose a severity for filter 1." }));
    await waitFor(() => expect(screen.getByLabelText("Filter audio-filter family")).toHaveFocus());
    expect(mcfExports).toBe(0);

    eligible = true;
    fireEvent.click(await exportAction("MCF"));
    const exportDialog = await screen.findByRole("dialog", { name: "Export" });
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Publish MCF" }));
    await waitFor(() => expect(mcfExports).toBe(1));
    await waitFor(() => expect(within(exportDialog).getByRole("listitem", { name: "MCF at Your personal files" })).toHaveTextContent("Current"));
    fireEvent.click(within(exportDialog).getByRole("button", { name: "Close" }));
    await openWorkflowStep("Review");
    const reviewRow = screen.getByRole("button", { name: "Open review Existing review" }).closest("tr")!;
    await waitFor(() => expect(reviewRow).toHaveTextContent("MCF · Current"));
    expect(reviewRow).toHaveTextContent("EDL · Current");

    revealMediaFiles();
    fireEvent.click(screen.getByRole("button", { name: "Manage legacy exports" }));
    const manageDialog = await screen.findByRole("dialog", { name: "Manage exports" });
    fireEvent.click(within(manageDialog).getByRole("button", { name: "Delete MCF" }));
    fireEvent.click(within(manageDialog).getByRole("button", { name: "Confirm Delete MCF" }));
    await waitFor(() => expect(mcfDeletes).toBe(1));
    expect(reviewRow).not.toHaveTextContent("MCF · Current");
    expect(reviewRow).toHaveTextContent("EDL · Current");
  });

});
