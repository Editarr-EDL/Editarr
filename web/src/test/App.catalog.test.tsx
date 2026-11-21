import { cleanup, fireEvent, render as renderComponent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { mediaItem, json, installProductionApi, completeScan, savedReview, reviewFilter } from "./app-test-fixtures";

describe("production saved-review UI authority", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads the saved-review panel for a catalog version", async () => {
    vi.stubGlobal("fetch", installProductionApi());
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    const mediaHeading = await screen.findByRole("heading", { name: "Example Movie" });
    const scansHeading = screen.getByRole("heading", { name: "Scans" });
    const importButton = screen.getByRole("button", { name: "Import" });
    const savedReviewsHeading = screen.getByRole("heading", { name: "Saved reviews" });
    expect(mediaHeading.compareDocumentPosition(scansHeading)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(scansHeading.compareDocumentPosition(savedReviewsHeading)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(savedReviewsHeading.compareDocumentPosition(importButton)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(savedReviewsHeading).toBeVisible();
    const savedReviewsTable = screen.getByRole("table", { name: "Saved reviews" });
    expect(savedReviewsTable).toBeVisible();
    const newReviewButton = screen.getByRole("button", { name: "New Review" });
    expect(newReviewButton.compareDocumentPosition(savedReviewsTable)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(importButton.compareDocumentPosition(savedReviewsTable)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    fireEvent.click(newReviewButton);
    expect(screen.getByRole("button", { name: "Start new review" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("New review name"), { target: { value: "Named review" } });
    expect(screen.getByRole("button", { name: "Start new review" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Start new review" }));
    expect(screen.getByRole("heading", { name: "Named review", level: 3 })).toBeVisible();
    expect(screen.queryByLabelText("Review name", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Review" })).toBeEnabled();
  });

  it("toggles library lengths between compact and MCF formats", async () => {
    const baseApi = installProductionApi();
    const itemWithMilliseconds = {
      ...mediaItem,
      duration_seconds: 3661.2345,
      versions: mediaItem.versions.map((version) => ({ ...version, duration_seconds: 3661.2345 })),
    };
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/libraries/movies/items") return json({ items: [itemWithMilliseconds], total_count: 1, rating_values: [], resolution_values: [] });
      if (url.pathname === "/api/library-items/movie-1" && (!init?.method || init.method === "GET")) return json(itemWithMilliseconds);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "List" }));
    const listLength = await screen.findByRole("button", { name: "Show exact length: 01:01:01.235" });
    expect(listLength).toHaveTextContent("1h 1m");
    expect(listLength).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(listLength);
    expect(listLength).toHaveTextContent("01:01:01.235");
    expect(listLength).toHaveAccessibleName("Show compact length: 1h 1m");
    expect(listLength).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(listLength);
    expect(listLength).toHaveTextContent("1h 1m");
    window.localStorage.removeItem("editarr.user.1.library-display.movies");

    fireEvent.click(screen.getByRole("button", { name: "Example Movie" }));
    expect(await screen.findByRole("heading", { name: "Example Movie" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Show exact length: 01:01:01.235" })).toHaveLength(2);
  });

  it("imports MCF uploads from the shared catalog dialog", async () => {
    const baseApi = installProductionApi();
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      requests.push(url.pathname);
      if (url.pathname === "/api/mcf-imports/upload" && init?.method === "POST") return json({
        review: savedReview({ name: "Imported MCF" }),
        scan: completeScan({ scan_id: 8, name: "Imported MCF", origin: "imported" }),
        source_version: "1.1.0",
        notices: [],
      }, 201);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = await screen.findByRole("dialog", { name: "Import" });
    fireEvent.click(within(dialog).getByRole("button", { name: "MCF" }));
    expect(within(dialog).getByLabelText("MCF file")).toHaveAttribute("accept", ".mcf,text/plain,text/mcf+vtt");
    expect(within(dialog).getByRole("button", { name: "Review Credits Result" })).toBeDisabled();
    expect(within(dialog).getByLabelText("Target START")).toHaveValue("00:00:00.000");
    expect(within(dialog).getByLabelText("Before-credits END")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Before-credits END"), { target: { value: "00:01:40.000" } });
    fireEvent.change(within(dialog).getByLabelText("Import review name"), { target: { value: "Imported MCF" } });
    fireEvent.change(within(dialog).getByLabelText("MCF file"), { target: { files: [new File(["WEBVTT"], "import.mcf", { type: "text/mcf+vtt" })] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import MCF" }));
    expect(await screen.findByRole("heading", { name: "Imported MCF", level: 3 })).toBeVisible();
    expect(requests).toContain("/api/mcf-imports/upload");
    expect(requests).not.toContain("/api/edl-imports/upload");
  });

  it("shows and hides the offending line for import errors", async () => {
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/mcf-imports/upload" && init?.method === "POST") return json({
        detail: {
          code: "invalid_mcf_metadata",
          message: "MCF metadata field is invalid",
          line_number: 7,
          line_text: "RATING unexpected",
        },
      }, 422);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = await screen.findByRole("dialog", { name: "Import" });
    fireEvent.click(within(dialog).getByRole("button", { name: "MCF" }));
    fireEvent.change(within(dialog).getByLabelText("Before-credits END"), { target: { value: "00:01:40.000" } });
    fireEvent.change(within(dialog).getByLabelText("Import review name"), { target: { value: "Invalid MCF" } });
    fireEvent.change(within(dialog).getByLabelText("MCF file"), { target: { files: [new File(["invalid"], "invalid.mcf", { type: "text/plain" })] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import MCF" }));

    expect(await within(dialog).findByText("MCF metadata field is invalid (line 7)")).toBeVisible();
    expect(within(dialog).queryByLabelText("Import error line")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Show Details" }));
    expect(within(dialog).getByLabelText("Import error line")).toHaveTextContent("RATING unexpected");
    fireEvent.click(within(dialog).getByRole("button", { name: "Hide Details" }));
    expect(within(dialog).queryByLabelText("Import error line")).not.toBeInTheDocument();
  });

  it("lists all MCF import issues and continues only after confirmation", async () => {
    const baseApi = installProductionApi();
    const submittedOptions: Array<{ ignore_errors: boolean; synchronization: unknown; mcf_material_end_seconds: number }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/mcf-imports/upload" && init?.method === "POST") {
        const options = JSON.parse(String((init.body as FormData).get("options"))) as { ignore_errors: boolean; synchronization: unknown; mcf_material_end_seconds: number };
        submittedOptions.push(options);
        if (!options.ignore_errors) return json({
          detail: {
            code: "mcf_import_issues",
            message: "Issues were found in the MCF file",
            line_number: null,
            line_text: null,
            import_issues: [
              { code: "invalid_mcf_metadata", message: "MCF metadata field has no valid value", line_number: 7, line_text: "SEASON " },
              { code: "invalid_mcf_document", message: "MCF material bounds exceed media duration", line_number: 13, line_text: "END 99:59:59.999" },
            ],
          },
        }, 422);
        return json({
          review: savedReview({ name: "Recovered MCF" }),
          scan: completeScan({ scan_id: 8, name: "Recovered MCF", origin: "imported" }),
          source_version: "1.1.0",
          notices: [],
        }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    let dialog = await screen.findByRole("dialog", { name: "Import" });
    fireEvent.click(within(dialog).getByRole("button", { name: "MCF" }));
    fireEvent.change(within(dialog).getByLabelText("Before-credits END"), { target: { value: "00:01:40.000" } });
    fireEvent.change(within(dialog).getByLabelText("Import review name"), { target: { value: "Recovered MCF" } });
    fireEvent.change(within(dialog).getByLabelText("MCF file"), { target: { files: [new File(["invalid"], "invalid.mcf", { type: "text/plain" })] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import MCF" }));

    let issuesDialog = await screen.findByRole("dialog", { name: "MCF import issues" });
    expect(within(issuesDialog).getAllByRole("listitem")).toHaveLength(2);
    expect(within(issuesDialog).getByText("SEASON").textContent).toBe("SEASON ");
    expect(within(issuesDialog).getByText("END 99:59:59.999")).toBeVisible();
    fireEvent.click(within(issuesDialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "MCF import issues" })).not.toBeInTheDocument();
    expect(submittedOptions.map((options) => options.ignore_errors)).toEqual([false]);

    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    dialog = await screen.findByRole("dialog", { name: "Import" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import MCF" }));
    issuesDialog = await screen.findByRole("dialog", { name: "MCF import issues" });
    fireEvent.click(within(issuesDialog).getByRole("button", { name: "Continue Anyway" }));

    expect(await screen.findByRole("heading", { name: "Recovered MCF", level: 3 })).toBeVisible();
    expect(submittedOptions.map((options) => options.ignore_errors)).toEqual([false, false, true]);
    expect(submittedOptions[2].synchronization).toEqual(submittedOptions[0].synchronization);
    expect(submittedOptions[2].mcf_material_end_seconds).toBe(submittedOptions[0].mcf_material_end_seconds);
  });

  it("imports an uploaded MCF with the selected target and offers no scanner path", async () => {
    const baseApi = installProductionApi();
    let requestBody: unknown = null;
    let uploaded: FormDataEntryValue | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/mcf-imports/upload" && init?.method === "POST") {
        const body = init.body as FormData;
        requestBody = JSON.parse(String(body.get("options")));
        uploaded = body.get("file");
        return json({ review: savedReview({ name: "Uploaded MCF" }), scan: completeScan({ scan_id: 9, name: "Uploaded MCF", origin: "imported" }), source_version: "1.0.0", notices: [] }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = await screen.findByRole("dialog", { name: "Import" });
    fireEvent.click(within(dialog).getByRole("button", { name: "MCF" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "File end" }));
    expect(within(dialog).getByLabelText("File END")).toHaveValue("00:02:00.000");
    fireEvent.change(within(dialog).getByLabelText("Canonical before-credits END"), { target: { value: "00:01:40.000" } });
    expect(within(dialog).queryByRole("button", { name: "Scanner path" })).toBeNull();
    expect(within(dialog).queryByRole("group", { name: "Import transport" })).toBeNull();
    expect(within(dialog).queryByLabelText(/scanner MCF path/i)).toBeNull();
    fireEvent.change(within(dialog).getByLabelText("Import review name"), { target: { value: "Uploaded MCF" } });
    const file = new File(["WEBVTT MovieContentFilter 1.0.0\n"], "example.mcf", { type: "text/plain" });
    fireEvent.change(within(dialog).getByLabelText("MCF file"), { target: { files: [file] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Import MCF" }));

    expect(await screen.findByRole("heading", { name: "Uploaded MCF", level: 3 })).toBeVisible();
    expect(uploaded).toBeInstanceOf(File);
    expect(requestBody).toEqual({
      name: "Uploaded MCF",
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
      ignore_errors: false,
      synchronization: { start_seconds: 0, end_seconds: 120, end_mode: "file_end" },
      mcf_material_end_seconds: 100,
      accepted_credits: null,
    });
  });

  it("adjusts all cue times in the draft and persists them only on Save", async () => {
    const filter = reviewFilter("audio-filter", "audio");
    let review = savedReview({ filters: [filter] });
    let retimeBody: Record<string, unknown> | null = null;
    let saveBody: Record<string, unknown> | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/reviews/review-1/retime" && method === "POST") {
        retimeBody = JSON.parse(String(init?.body));
        return json({ synchronization: { start_seconds: 10, end_seconds: 110, end_mode: "before_credits" }, mcf_document: { ...review.mcf_document, material_start_seconds: 10, material_end_seconds: 110 }, filters: [{ ...filter, start_seconds: 11, end_seconds: 12 }], accepted_credits: null, new_manual_filters: [] });
      }
      if (url.pathname === "/api/reviews/review-1" && method === "PUT") {
        saveBody = JSON.parse(String(init?.body));
        review = { ...review, ...(saveBody as object), revision: 2, synchronization: { start_seconds: 10, end_seconds: 110, end_mode: "before_credits" } };
        return json(review);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Adjust Cue Times" }));
    const dialog = screen.getByRole("dialog", { name: "Adjust Cue Times" });
    expect(within(dialog).queryByRole("button", { name: "Review Credits Result" })).not.toBeInTheDocument();
    expect(within(dialog).getByText(/Only imported or unverified cues are adjusted/)).toBeVisible();
    fireEvent.change(within(dialog).getByLabelText("Proposed START"), { target: { value: "00:00:10.000" } });
    fireEvent.change(within(dialog).getByLabelText("Proposed synchronization END"), { target: { value: "00:01:50.000" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply to draft" }));

    await waitFor(() => expect(retimeBody).not.toBeNull());
    expect(retimeBody).toMatchObject({
      memberships: review.memberships,
      accepted_credits: null,
      current_synchronization: review.synchronization,
      proposed_synchronization: { start_seconds: 10, end_seconds: 110, end_mode: "before_credits" },
      mcf_document: review.mcf_document,
    });
    expect(saveBody).toBeNull();
    expect(screen.getAllByText(/00:00:10\.000–00:01:50\.000/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(saveBody).not.toBeNull());
    expect(saveBody).toMatchObject({ synchronization: { start_seconds: 10, end_seconds: 110, end_mode: "before_credits" }, filters: [{ start_seconds: 11, end_seconds: 12 }] });
  });

  it("reviews and stages an edited low-confidence credits result", async () => {
    let review = savedReview();
    const creditsScan = completeScan({
      id: "credits-1", scan_id: 8, name: "End Credits detection", scan_mode: "credits_detection",
      scan_audio: false, audio_model: null, audio_filter: null, scan_video: true, video_model: null,
      credits_detection: {
        configuration: { detector_version: "credits-detector-v1", tail_window_seconds: 1200, frame_sample_interval_seconds: 2, qwen_model: null },
        detector_version: "credits-detector-v1", cache_key: "safe-key", completed_at: "2026-09-20T10:00:00Z",
        result: { outcome: "candidate", best_candidate_id: "candidate-1", candidates: [{ id: "candidate-1", boundary_seconds: 100, range_start_seconds: 96, range_end_seconds: 104, confidence: 0.55, confidence_band: "low", method: "hybrid", evidence: [] }] },
      },
    });
    let acceptedBoundary: number | null = null;
    let saveBody: Record<string, unknown> | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([creditsScan]);
      if (url.pathname === "/api/library-items/movie-1/reviews") return json([review]);
      if (url.pathname === "/api/reviews/review-1" && method === "GET") return json(review);
      if (url.pathname === "/api/credits-detections/credits-1/accept" && method === "POST") {
        acceptedBoundary = (JSON.parse(String(init?.body)) as { accepted_boundary_seconds: number }).accepted_boundary_seconds;
        return json({ accepted_credits: { scan_id: "credits-1", candidate_id: "candidate-1", accepted_boundary_seconds: acceptedBoundary }, membership: { scan_id: "credits-1", role: "video" }, filter: { ...reviewFilter("credits-filter", "visual"), source_scan_id: "credits-1", source_finding_id: 42, category: "dispensable_scenes", subcategory: "dispensable", mcf_channel: "video", mcf_comment: "End Credits", label: "End Credits", details: "End Credits", start_seconds: acceptedBoundary, end_seconds: 120, confidence: 0.55, edit_action: "skip" } });
      }
      if (url.pathname === "/api/reviews/review-1" && method === "PUT") {
        saveBody = JSON.parse(String(init?.body));
        review = { ...review, ...(saveBody as object), revision: 2 };
        return json(review);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review Credits Result" }));
    const dialog = screen.getByRole("dialog", { name: "Review Credits Result" });
    expect(within(dialog).queryByRole("button", { name: "Start detection" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("progressbar")).not.toBeInTheDocument();
    expect(within(dialog).getByText(/Low confidence/)).toBeVisible();
    expect(within(dialog).getByLabelText("Credits boundary preview")).toHaveAttribute("src", "/api/credits-detections/credits-1/clip?boundary_seconds=100");
    fireEvent.change(within(dialog).getByLabelText("Accepted credits boundary"), { target: { value: "00:01:42.000" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Accept boundary" }));
    await waitFor(() => expect(acceptedBoundary).toBe(102));
    expect(screen.getByText(/Accepted credits · 00:01:42\.000/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Save Changes" }));
    await waitFor(() => expect(saveBody).not.toBeNull());
    expect(saveBody).toMatchObject({ accepted_credits: { scan_id: "credits-1", candidate_id: "candidate-1", accepted_boundary_seconds: 102 }, memberships: [{ scan_id: "credits-1", role: "video" }], mcf_document: { material_end_seconds: 102 } });
  });

  it("shows credits scan progress only in the standard queue", async () => {
    const scanning = {
      ...completeScan({ id: "credits-active", scan_id: 10, name: "End Credits detection", scan_mode: "credits_detection", status: "scanning", scan_audio: false, scan_video: true, video_model: null, credits_detection: { configuration: { detector_version: "credits-detector-v1", tail_window_seconds: 45, frame_sample_interval_seconds: 2, qwen_model: null }, detector_version: "credits-detector-v1", cache_key: null, result: null, completed_at: null } }),
      queue_id: 12,
      progress_percent: 35,
      progress_stage: "credits_analysis",
      progress_message: "Analyzing video tail",
    };
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/queue") return json([scanning]);
      if (url.pathname === "/api/scans/history") return json([scanning]);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    expect(await screen.findByRole("heading", { name: "Example Movie" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Detect Credits" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review Credits Result" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open scans queue and history" }));
    const queue = await screen.findByRole("region", { name: "Scans queue and history" });
    expect(within(queue).getByText("Analyzing video tail")).toBeVisible();
    expect(within(queue).getByRole("progressbar", { name: /End Credits detection.*progress/ })).toHaveValue(35);
    expect(screen.queryByRole("dialog", { name: /Credits/ })).not.toBeInTheDocument();
  });

  it("submits Unicode scan names with the selected Severity and Description model and refreshes a validated rename", async () => {
    let scan = completeScan();
    const queuedNames: Array<string | null> = [];
    const queuedQwenModels: string[] = [];
    const renamedNames: Array<string | null> = [];
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      const method = init?.method || "GET";
      if (url.pathname === "/api/scans/history") return json([scan]);
      if (url.pathname === "/api/scans" && method === "POST") {
        const payload = JSON.parse(String(init?.body)) as {
          name: string | null;
          qwen_model: string;
        };
        queuedNames.push(payload.name);
        queuedQwenModels.push(payload.qwen_model);
        return json({ ...scan, id: "queued-scan", scan_id: 8, status: "queued", name: payload.name }, 201);
      }
      if (url.pathname === "/api/scans/scan-combined" && method === "PATCH") {
        const payload = JSON.parse(String(init?.body)) as { name: string | null };
        renamedNames.push(payload.name);
        scan = { ...scan, name: payload.name };
        return json(scan);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    fireEvent.change(await screen.findByLabelText("Scan type"), {
      target: { value: "video" },
    });
    fireEvent.change(await screen.findByLabelText("Severity and Description model"), {
      target: { value: "qwen3-vl-4b-instruct-4bit" },
    });
    const scanName = await screen.findByLabelText("Scan name (optional)");
    const emojiName = "😀".repeat(100);
    expect(scanName).not.toHaveAttribute("maxlength");
    fireEvent.change(scanName, { target: { value: emojiName } });
    fireEvent.click(screen.getByRole("button", { name: "Add to Queue" }));
    await waitFor(() => expect(queuedNames).toEqual([emojiName]));
    expect(queuedQwenModels).toEqual(["qwen3-vl-4b-instruct-4bit"]);

    const renameTrigger = await screen.findByRole("button", { name: "Rename Combined scan · Scan #7" });
    expect(renameTrigger).toHaveTextContent("✎");
    expect(renameTrigger.closest(".scan-history-title")).not.toBeNull();
    expect(screen.queryByText("Rename", { selector: "button" })).not.toBeInTheDocument();
    fireEvent.click(renameTrigger);
    const renameInput = screen.getByLabelText("Name for Combined scan · Scan #7");
    fireEvent.change(renameInput, { target: { value: "invalid\u0007name" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Name contains a disallowed Unicode character.");
    const renameEditor = renameInput.closest(".scan-rename-editor") as HTMLElement;
    expect(within(renameEditor).getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(renameInput, { target: { value: emojiName } });
    fireEvent.click(within(renameEditor).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(renamedNames).toEqual([emojiName]));
    expect(await screen.findByRole("button", { name: `Rename ${emojiName} · Scan #7` })).toBeVisible();
  });

  it("queues credits detection from the regular scan form without a saved review", async () => {
    const creditsScan = completeScan({
      id: "credits-queued",
      scan_id: 8,
      status: "queued",
      scan_mode: "credits_detection",
      scan_audio: false,
      audio_model: null,
      audio_filter: null,
      scan_video: true,
      video_model: null,
      credits_detection: {
        configuration: { detector_version: "credits-detector-v1", tail_window_seconds: 45, frame_sample_interval_seconds: 2, qwen_model: null },
        detector_version: "credits-detector-v1",
        cache_key: null,
        result: null,
        completed_at: null,
      },
    });
    let requestBody: unknown = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/credits-detections" && init?.method === "POST") {
        requestBody = JSON.parse(String(init.body));
        return json({ scan: creditsScan, queue_id: 9, model_consent: null }, 202);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    fireEvent.change(within(dialog).getByLabelText("Scan type"), { target: { value: "credits" } });
    expect(within(dialog).getByText(/local chapter and visual analysis/)).toBeVisible();
    expect(within(dialog).queryByLabelText("Video model")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add to Queue" }));

    await waitFor(() => expect(requestBody).toEqual({
      target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
      qwen_model: null,
    }));
    expect(await screen.findByText("Credits scan 8 added to the queue.")).toBeVisible();
  });

  it("shows the stored reason for a failed scan", async () => {
    const failedScan = completeScan({ status: "failed", error_message: "decoder failed on the selected video stream" });
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/scans/history") return json([failedScan]);
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    renderComponent(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    expect(await screen.findByText("decoder failed on the selected video stream")).toBeVisible();
  });

});
