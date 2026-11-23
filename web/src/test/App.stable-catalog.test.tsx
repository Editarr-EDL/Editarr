import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "../App";
import type { AudioTrack, AudioTrackSelection } from "../api/api-audio-tracks";
import type { EditarrItem, VerificationPreview, VerificationRequest } from "../api/api-stable-media";
import { AuthenticationGate } from "../auth/auth-gate";
import { notifyScansChanged } from "../shared/scan-events";
import { completeScan, installProductionApi, json, mediaItem, savedReview } from "./app-test-fixtures";
import { openWorkflowStep } from "./review-workflow-test-helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

it("opens the old bookmark on its historical version and verifies cues explicitly for the new cut", async () => {
  const current = { ...mediaItem, name: "The Life of Chuck" };
  const scan = completeScan({
    id: "old-video-scan",
    name: "Original video scan",
    jellyfin_item_id: "old-jellyfin-id",
    media_source_id: "old-source",
    scan_audio: false,
  });
  const review = savedReview({ id: "old-review", name: "Original saved review", revision: 3 });
  const record: EditarrItem = {
    title_id: "editarr-title",
    name: current.name,
    active_item: current,
    active_items: [current],
    selected_version_id: "original-cut",
    choose_version: false,
    current_versions: [{ item_id: "movie-1", media_source_id: "source-1", version_id: "new-cut" }],
    versions: [{
      id: "original-cut",
      jellyfin_item_id: "old-jellyfin-id",
      media_source_id: "old-source",
      label: "Original Blu-ray",
      available: false,
      active_item_id: null,
      active_media_source_id: null,
      scans: [scan, { ...scan, id: "another-source", name: "Other earlier scan" }],
      reviews: [{ id: review.id, name: review.name }],
    }],
  };
  const fallback = installProductionApi();
  const verificationRequests: VerificationRequest[] = [];
  const approvals: unknown[] = [];
  const adopted = completeScan({
    id: "approved-copy", name: "Adopted video scan", origin: "imported", scan_audio: false,
    verified_from_scan_id: scan.id, stable_version_id: "new-cut",
    verification_coverage: {
      source_counts: { audio: 0, video: 2 },
      covered_finding_ids: { audio: [], video: [1] },
    },
  });
  let saved = false;
  const token = "a".repeat(64);
  let slowPreview: AbortSignal | undefined;
  let holdPreview = false;
  let fingerprinting = false;
  let finishApproval: (() => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/legacy/old-jellyfin-id") {
      expect(url.searchParams.get("library_id")).toBe("movies");
      return json(record);
    }
    if (url.pathname === "/api/editarr-items/editarr-title") return json(record);
    if (url.pathname === "/api/scans/history") return json(saved ? [adopted] : []);
    if (url.pathname === "/api/reviews/old-review") return json(review);
    if (url.pathname === "/api/media-versions/current/reuse/preview") {
      if (holdPreview) {
        slowPreview = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          slowPreview?.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
        });
      }
      const request: VerificationRequest = JSON.parse(String(init?.body));
      verificationRequests.push(request);
      const preview: VerificationPreview = {
        source_scan_id: scan.id, version_id: "current", token, exact: false,
        verified_channels: ["video"], issues: [], anchors: request.anchors || [],
        cues: [
          { source_finding_id: 1, channel: "video", source_start: 1, source_end: 2, target_start: 3, target_end: 4 },
          { source_finding_id: 2, channel: "video", source_start: 32, source_end: 35, target_start: null, target_end: null },
        ],
      };
      return json(preview);
    }
    if (url.pathname === "/api/media-versions/current/reuse/approve") {
      approvals.push(JSON.parse(String(init?.body)));
      fingerprinting = true;
      return new Promise<Response>((resolve) => {
        finishApproval = () => {
          fingerprinting = false;
          saved = true;
          resolve(json({ scan: adopted, reused: false }));
        };
      });
    }
    if (url.pathname === "/api/media-fingerprints/queue") {
      return json(fingerprinting ? [{
        id: "fingerprint-1", item_name: current.name, version_name: "Current remux",
        source_scan_id: scan.id, item_id: "movie-1", media_source_id: "source-1",
        bytes_read: 180, bytes_total: 600, reading: true,
      }] : []);
    }
    return fallback(input, init);
  }));

  window.location.hash = "#/library/movies/item/old-jellyfin-id";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await waitFor(() => expect(window.location.hash).toBe("#title/editarr-title/version/original-cut"));
  expect(await screen.findByText("Original Blu-ray · Previous version", { selector: "p" })).toBeVisible();
  expect(screen.queryByRole("tab", { name: "Publish" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: review.name }));
  expect(await screen.findByText(/Revision 3/)).toBeVisible();

  expect(screen.queryByRole("combobox", { name: "Item history" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open current version" }));
  await openWorkflowStep("Scans");
  const previous = screen.getByRole("region", { name: "Previous version scans" });
  const original = within(previous).getByText(/Original video scan/).closest("li")!;
  expect(within(original).getByText(/Needs verification for this version/)).toBeVisible();
  fireEvent.click(within(original).getByRole("button", { name: "Verify for this version" }));
  const dialog = screen.getByRole("dialog", { name: "Verify for this version" });
  const timing = within(dialog).getByRole("combobox", { name: "Timing method" });
  expect(timing).toHaveValue("manual");
  for (const [label, value] of [["Original time 1", "0"], ["Target time 1", "2"], ["Original time 2", "30"], ["Target time 2", "32"]]) {
    fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
  }
  fireEvent.click(within(dialog).getByRole("button", { name: "Preview alignment" }));
  const continueToApproval = await within(dialog).findByRole("button", { name: "Continue to approval" });
  expect(within(dialog).getByText(/1 pending/i)).toBeVisible();
  expect(verificationRequests).toEqual([expect.objectContaining({
    source_scan_id: scan.id, channels: ["video"], method: "manual",
    target: { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" },
    anchors: [{ source_seconds: 0, target_seconds: 2 }, { source_seconds: 30, target_seconds: 32 }],
  })]);
  expect(approvals).toEqual([]);
  fireEvent.click(continueToApproval);
  const approve = await within(dialog).findByRole("button", { name: "Approve for this version" });
  expect(approve).toBeEnabled();
  expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(approvals).toEqual([]);
  fireEvent.click(approve);
  const verificationQueueToggle = within(dialog).getByRole("button", { name: /^Queue\b/i });
  if (verificationQueueToggle.getAttribute("aria-expanded") !== "true") fireEvent.click(verificationQueueToggle);
  const verificationQueue = await within(dialog).findByRole("region", { name: "Verification Queue" });
  await waitFor(() => expect(within(verificationQueue).getByRole("progressbar", { name: "File fingerprint progress" })).toHaveAttribute("value", "30"));
  fireEvent.click(within(dialog).getByRole("button", { name: "Continue in Queue" }));
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
  const queue = await screen.findByRole("region", { name: "Scans queue and history" });
  const fingerprints = await within(queue).findByRole("region", { name: "File fingerprints" });
  expect(within(fingerprints).getByText("Calculating fingerprint · 30%")).toBeVisible();
  expect(within(fingerprints).getByText("Current remux")).toBeVisible();
  finishApproval?.();
  await waitFor(() => expect(within(queue).queryByRole("region", { name: "File fingerprints" })).not.toBeInTheDocument());
  expect(approvals).toEqual([expect.objectContaining({ source_scan_id: scan.id, token, confirmed: true })]);

  await waitFor(() => expect(screen.getAllByText(/Adopted video scan/).length).toBeGreaterThan(0));
  expect(within(original).getByRole("button", { name: "Verify for this version" })).toBeVisible();
  const other = within(previous).getByText(/Other earlier scan/).closest("li")!;
  holdPreview = true;
  fireEvent.click(within(other).getByRole("button", { name: "Verify for this version" }));
  const slowDialog = screen.getByRole("dialog", { name: "Verify for this version" });
  fireEvent.change(within(slowDialog).getByRole("combobox", { name: "Timing method" }), { target: { value: "original" } });
  fireEvent.click(within(slowDialog).getByRole("button", { name: "Preview alignment" }));
  await waitFor(() => expect(slowPreview).toBeDefined());
  expect(within(slowDialog).getByText("Calculating proposed cue times…")).toBeVisible();
  fireEvent.click(within(slowDialog).getByRole("button", { name: "Cancel" }));
  expect(slowDialog).not.toBeInTheDocument();
  expect(slowPreview?.aborted).toBe(true);
});

it("lists associated scans with their original source ID and keeps them pending verification", async () => {
  const scan = completeScan({ id: "associated-old", name: "Linked earlier scan", jellyfin_item_id: "original",
    media_source_id: "original-source" });
  const record: EditarrItem = {
    title_id: "stable-chuck", name: mediaItem.name, active_item: mediaItem, active_items: [mediaItem],
    selected_version_id: null, choose_version: false, versions: [],
  };
  const fallback = installProductionApi();
  let linked = false;
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/stable-chuck") return json(record);
    if (url.pathname === "/api/filter-candidates") {
      return json({ media_item_id: "resolved-media", matches: [{ scan, match_reason: "format", already_associated: false }] });
    }
    if (url.pathname === "/api/filter-candidates/associations") {
      linked = true;
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/api/library-items/movie-1/scans") {
      expect(url.searchParams.get("associated_only")).toBe("true");
      return json(linked ? [scan] : []);
    }
    if (url.pathname === "/api/scans/history") return json([scan]);
    if (url.pathname === "/api/scans/queue") return json([]);
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetchMock);

  window.location.hash = "#title/stable-chuck";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  fireEvent.click(screen.getByRole("button", { name: "Find Filters" }));
  fireEvent.click(await screen.findByRole("button", { name: "Associate Selected" }));
  const previous = await screen.findByRole("region", { name: "Previous version scans" });
  expect(within(previous).getByText(/^Linked earlier scan · Scan #7$/)).toBeVisible();
  expect(within(previous).getByText(/Needs verification for this version/)).toBeVisible();
  expect(fetchMock.mock.calls.some(([input, init]) =>
    String(input).endsWith("/api/scans") && init?.method === "POST")).toBe(false);
});

it("checks a saved transcript for imported cues and offers manual timing if confidence is low", async () => {
  const scan = completeScan({
    id: "downloaded-audio", name: "Downloaded audio", origin: "imported",
    scan_audio: true, scan_video: false, jellyfin_item_id: "original", media_source_id: "earlier",
  });
  const record: EditarrItem = {
    title_id: "imported-item", name: mediaItem.name, active_item: mediaItem, active_items: [mediaItem],
    selected_version_id: null, choose_version: false, versions: [{
      id: "earlier-cut", jellyfin_item_id: "original", media_source_id: "earlier", label: "Earlier cut",
      available: false, scans: [scan], reviews: [],
    }],
  };
  const fallback = installProductionApi();
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/imported-item") return json(record);
    if (url.pathname === "/api/library-items/movie-1/scans") return json([]);
    if (url.pathname === "/api/media-versions/current/reuse/transcript-status") {
      return json({ ready: false, verify_existing: true });
    }
    if (url.pathname === "/api/media-versions/current/reuse/preview") {
      expect(JSON.parse(String(init?.body))).toMatchObject({
        source_scan_id: scan.id, method: "transcript", channels: ["audio"], audio_track_index: 1,
      });
      return json({
        source_scan_id: scan.id, version_id: "current", token: null, exact: false,
        verified_channels: [], issues: ["Couldn’t align automatically with enough confidence."],
        anchors: [], cues: [],
      } satisfies VerificationPreview);
    }
    return fallback(input, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  window.location.hash = "#title/imported-item";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  fireEvent.click(await screen.findByRole("button", { name: "Verify for this version" }));
  const dialog = screen.getByRole("dialog", { name: "Verify for this version" });
  expect(within(dialog).getByLabelText("Timing method")).toHaveValue("transcript");
  expect(await within(dialog).findByText("Existing audio scan found")).toBeVisible();
  expect(within(dialog).queryByRole("button", { name: "Run audio scan" })).not.toBeInTheDocument();
  const preview = within(dialog).getByRole("button", { name: "Preview alignment" });
  await waitFor(() => expect(preview).toBeEnabled());
  fireEvent.click(preview);
  const manual = await within(dialog).findByRole("button", { name: "Use manual alignment" });
  expect(within(dialog).getByText(/Couldn't align automatically|Couldn’t align automatically/)).toBeVisible();
  expect(within(dialog).queryByRole("button", { name: "Continue to approval" })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Approve for this version" })).not.toBeInTheDocument();
  fireEvent.click(manual);
  expect(within(dialog).getByLabelText("Timing method")).toHaveValue("manual");
  expect(within(dialog).getByLabelText("Original time 1")).toBeVisible();
});

it.each(["unchanged", "new file generation", "changed track metadata"])("queues an exact verification audio selection after %s", async (change) => {
  const scan = completeScan({
    id: "original-scan", name: "Original Scan", scan_video: false,
    jellyfin_item_id: "earlier-item", media_source_id: "earlier-source",
  });
  const record: EditarrItem = {
    title_id: "audio-alignment-item", name: mediaItem.name, active_item: mediaItem, active_items: [mediaItem],
    selected_version_id: null, choose_version: false, versions: [{
      id: "earlier-version", jellyfin_item_id: "earlier-item", media_source_id: "earlier-source",
      label: "Old Blu-ray", available: false, scans: [scan], reviews: [],
    }],
  };
  const primary: AudioTrack = {
    stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 6, disposition: 1,
    start_seconds: 0, duration_seconds: 120, time_base: "1/48000",
  };
  const alternate = { ...primary, stream_index: 4, title: "Alternate", disposition: 0 };
  let fileVersion = "b".repeat(64);
  let currentAlternate = alternate;
  let selection: AudioTrackSelection = { file_version: fileVersion, track: primary };
  let job: "none" | "scanning" | "complete" = "none";
  const targetScope = { item_id: "movie-1", media_source_id: "source-1" };
  const jobScan = completeScan({ id: "target-audio", scan_id: 19, scan_video: false, name: "Target audio" });
  const enqueueRequests: unknown[] = [];
  const remembered: AudioTrackSelection[] = [];
  const previewRequests: VerificationRequest[] = [];
  const fallback = installProductionApi();
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/audio-alignment-item") return json(record);
    if (url.pathname === "/api/media/audio-tracks") {
      expect(Object.fromEntries(url.searchParams)).toMatchObject(targetScope);
      return json({ file_version: fileVersion, tracks: [primary, currentAlternate], selection, selection_stale: false });
    }
    if (url.pathname === "/api/media/audio-tracks/selection") {
      const payload = JSON.parse(String(init?.body));
      expect(payload).toMatchObject(targetScope);
      selection = payload.selection;
      remembered.push(selection);
      return json(selection);
    }
    if (url.pathname === "/api/media-versions/current/reuse/transcript-status") {
      const request: VerificationRequest = JSON.parse(String(init?.body));
      expect(request).toMatchObject({ source_scan_id: scan.id, target: { source: "jellyfin", ...targetScope } });
      return json({ ready: job === "complete" && request.audio_track_index === alternate.stream_index });
    }
    const activeJob = {
      ...jobScan, status: "scanning", queue_id: 3, sort_order: 0,
      requested_audio_selection: { file_version: fileVersion, track: currentAlternate },
      progress_percent: 42, progress_stage: "transcription", progress_message: "Transcribing audio",
    };
    if (url.pathname === "/api/scans" && init?.method === "POST") {
      enqueueRequests.push(JSON.parse(String(init.body)));
      job = "scanning";
      return json(activeJob);
    }
    if (url.pathname === "/api/scans/queue") return json(job === "scanning" ? [activeJob]
      : job === "complete" ? [{ ...activeJob, status: "complete" }] : []);
    if (url.pathname === "/api/scans/history") return json(job === "complete"
      ? [{ ...jobScan, analyzed_audio_selection: activeJob.requested_audio_selection }] : []);
    if (url.pathname === "/api/media-versions/current/reuse/preview") {
      previewRequests.push(JSON.parse(String(init?.body)));
      return json({
        source_scan_id: scan.id, version_id: "current", token: "a".repeat(64), exact: false,
        anchors: [], verified_channels: ["audio"], issues: [],
        cues: [{ source_finding_id: 8, channel: "audio", source_start: 4, source_end: 5, target_start: 6, target_end: 7 }],
      } satisfies VerificationPreview);
    }
    return fallback(input, init);
  }));

  window.location.hash = "#title/audio-alignment-item";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  fireEvent.click(await screen.findByRole("button", { name: "Verify for this version" }));
  const dialogName = "Verify for this version";
  const dialog = within(screen.getByRole("dialog", { name: dialogName }));
  await dialog.findByText("Target transcript needed");
  const trackPicker = dialog.getByRole("combobox", { name: /Audio track/i });
  fireEvent.change(trackPicker, { target: { value: "4" } });
  await waitFor(() => expect(trackPicker).toHaveValue("4"));
  const run = await dialog.findByRole("button", { name: "Run audio scan" });
  await waitFor(() => expect(run).toBeEnabled());
  if (change !== "unchanged") {
    if (change === "new file generation") fileVersion = "c".repeat(64);
    else currentAlternate = { ...alternate, title: "Replacement audio" };
    selection = { file_version: fileVersion, track: primary };
  }
  fireEvent.click(run);

  const newScan = within(await screen.findByRole("dialog", { name: "New Scan" }));
  const queueScan = newScan.getByRole("button", { name: "Add to Queue" });
  const currentTrack = newScan.getByRole("combobox", { name: "Audio track" });
  if (change !== "unchanged") {
    expect(await newScan.findByRole("alert")).toHaveTextContent("Audio selection changed. Choose a track again.");
    expect(currentTrack).toHaveValue("");
    expect(queueScan).toBeDisabled();
    expect(remembered).toHaveLength(1);
    expect(enqueueRequests).toEqual([]);
    fireEvent.change(currentTrack, { target: { value: "4" } });
    await waitFor(() => expect(remembered).toHaveLength(2));
  }
  await waitFor(() => expect(currentTrack).toHaveValue("4"));
  await waitFor(() => expect(queueScan).toBeEnabled());
  fireEvent.click(queueScan);
  const activeVerification = within(await screen.findByRole("dialog", { name: dialogName }));
  expect(enqueueRequests).toEqual([expect.objectContaining({
    jellyfin_item_id: targetScope.item_id, media_source_id: targetScope.media_source_id,
    scan_audio: true, scan_video: false, audio_selection: { file_version: fileVersion, track: currentAlternate },
  })]);
  expect(previewRequests).toEqual([]);
  const queue = activeVerification.getByRole("button", { name: /^Queue\b/i });
  if (queue.getAttribute("aria-expanded") !== "true") fireEvent.click(queue);
  expect(await activeVerification.findByRole("progressbar")).toHaveAttribute("value", "42");

  job = "complete";
  notifyScansChanged();
  const resumed = within(await screen.findByRole("dialog", { name: dialogName }));
  const preview = await resumed.findByRole("button", { name: "Preview alignment" });
  await waitFor(() => expect(preview).toBeEnabled());
  fireEvent.click(preview);
  expect(await resumed.findByText("1 aligned")).toBeVisible();
  expect(previewRequests).toEqual([expect.objectContaining({
    source_scan_id: scan.id, target: { source: "jellyfin", ...targetScope },
    channels: ["audio"], method: "transcript", audio_track_index: 4,
  })]);
});
