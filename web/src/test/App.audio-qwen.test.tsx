import { openWorkflowStep } from "./review-workflow-test-helpers";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { completeScan, installProductionApi, json, mediaItem, signedInStatus } from "./app-test-fixtures";
import { chooseModel, chooseScanType, expectSelectedModel, infoText, modelPicker, scanTypeRadio, type ScanTypeName } from "./new-scan-test-helpers";

const qwen38 = {
  name: "Qwen3.8-27B",
  qwenId: "qwen38-27b-instruct-4bit",
  videoId: "qwen38-27b",
};

const audioCapabilities = {
  audio_models: [
    { id: "whisper", display_name: "Whisper" },
    { id: "apple_speech", display_name: "Apple Speech" },
  ],
  audio_filters: [
    { id: "better_profanity", display_name: "better-profanity" },
    { id: "qwen", display_name: "Qwen (multilingual)" },
  ],
  video_models: [
    { id: "nudenet", display_name: "NudeNet" },
    { id: qwen38.videoId, display_name: qwen38.name },
  ],
  qwen_models: [
    { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B" },
    { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B" },
    { id: qwen38.qwenId, display_name: qwen38.name },
  ],
  model_capabilities: [],
};

describe("Qwen language scan controls", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it.each([
    ...["audio", "video", "combined"].map((mode) => ({
      mode, qwenName: "Qwen3-VL 4B", qwenId: "qwen3-vl-4b-instruct-4bit", videoName: "NudeNet", videoId: "nudenet",
    })),
    { mode: "combined", qwenName: qwen38.name, qwenId: qwen38.qwenId, videoName: qwen38.name, videoId: qwen38.videoId },
  ])("submits a path-free catalog $mode scan with $qwenName", async ({ mode, qwenName, qwenId, videoName, videoId }) => {
    let request: Record<string, unknown> | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/auth/status") return json(signedInStatus({ userId: 4, role: "user", capabilities: ["media.browse", "scans.start", "scan_jobs.view.own", "reviews.view.own", "system.version.view"] }));
      const safeItem = { ...mediaItem, path: null, versions: mediaItem.versions.map((version) => ({ ...version, path: null })) };
      if (url.pathname === "/api/library-items/movie-1") return json(safeItem);
      if (url.pathname === "/api/libraries/movies/items") return json({ items: [safeItem], total_count: 1 });
      if (url.pathname === "/api/capabilities") return json(audioCapabilities);
      if (url.pathname === "/api/scans" && init?.method === "POST") {
        request = JSON.parse(String(init.body));
        return json({ ...completeScan(), id: "queued-audio", scan_id: 8, status: "queued" }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    expect(scanTypeRadio(dialog, "Audio")).toBeChecked();
    chooseScanType(dialog, ({ audio: "Audio", video: "Video", combined: "Audio and video" } as Record<string, ScanTypeName>)[mode]);
    if (mode !== "video") {
      await expectSelectedModel(dialog, "Transcriber", "Whisper");
      await chooseModel(dialog, "Transcriber", "Apple Speech");
      expect(within(dialog).getByText(/Apple Speech runs on the Editarr server/)).toBeVisible();
      await chooseModel(dialog, "Filter", "Qwen");
    }
    if (mode !== "audio") {
      await chooseModel(dialog, "Video model", videoName);
      fireEvent.change(within(dialog).getByLabelText("Representative frames per flagged segment"), { target: { value: "6" } });
      fireEvent.change(within(dialog).getByLabelText("Join flagged segments within (seconds)"), { target: { value: "2" } });
    }
    await chooseModel(dialog, "Severity and Description model", qwenName);
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Scan name (optional)" }), {
      target: { value: `Selected ${mode}` },
    });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Add to Queue" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Add to Queue" }));

    await waitFor(() => expect(request).not.toBeNull());
    expect(request).toMatchObject({
      name: `Selected ${mode}`,
      media_source_id: "source-1",
      scan_audio: mode !== "video",
      scan_video: mode !== "audio",
      audio_model: mode === "video" ? null : "apple_speech",
      audio_filter: mode === "video" ? null : "qwen",
      qwen_model: qwenId,
    });
    if (mode !== "video") expect(request).toMatchObject({ audio_selection: { file_version: "a".repeat(64), track: { stream_index: 1, title: "Main" } } });
    if (mode !== "audio") expect(request).toMatchObject({ video_model: videoId, qwen_summaries_per_segment: 6, qwen_segment_join_gap_seconds: 2 });
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("path-mappings"))).toBe(false);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => /\/models(?:[/?]|$)|\/settings\//.test(String(input)))).toBe(false);
    expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input).includes("audio-tracks")).every(([input, init]) => !String(input).includes("video_path") && !String(init?.body).includes("video_path"))).toBe(true);
  });

  it("submits the observed audio track directly and keeps queue errors in the New Scan dialog", async () => {
    const baseApi = installProductionApi();
    const submit = vi.fn();
    const confirm = vi.fn();
    let finishFirstSubmit: ((response: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/capabilities") return json(audioCapabilities);
      if (url.pathname === "/api/media/audio-tracks/selection") {
        confirm();
        throw new Error("Audio selection confirmation response was interrupted");
      }
      if (url.pathname === "/api/scans" && init?.method === "POST") {
        submit(JSON.parse(String(init.body)));
        return submit.mock.calls.length === 1
          ? new Promise<Response>((resolve) => { finishFirstSubmit = resolve; })
          : json({ ...completeScan(), id: "queued-audio", scan_id: 9, status: "queued" }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(screen.getByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    const add = within(dialog).getByRole("button", { name: "Add to Queue" });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    await waitFor(() => expect(submit).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      media_source_id: "source-1",
      audio_selection: expect.objectContaining({ file_version: "a".repeat(64), track: expect.objectContaining({ stream_index: 1 }) }),
    })));
    expect(confirm).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText("Version")).toBeDisabled();
    for (const name of ["Audio", "Video", "Audio and video", "Credits"] as const) expect(scanTypeRadio(dialog, name)).toBeDisabled();
    await act(async () => { finishFirstSubmit?.(json({ detail: "Configure Jellyfin before creating a scan" }, 409)); });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Configure Jellyfin before creating a scan");
    expect(dialog).toBeVisible();
    expect(within(dialog).queryByRole("button", { name: "Refresh audio tracks" })).not.toBeInTheDocument();
    expect(screen.queryByText("Scan 9 added to the queue.")).not.toBeInTheDocument();

    fireEvent.click(add);
    expect(await screen.findByText("Scan 9 added to the queue.")).toBeVisible();
    expect(screen.queryByRole("dialog", { name: "New Scan" })).not.toBeInTheDocument();
    expect(submit).toHaveBeenCalledTimes(2);
    expect(confirm).not.toHaveBeenCalled();
  });

  it.each([
    "Audio selection is stale; refresh and choose a track again",
    "Choose an audio track before starting the scan",
  ])("allows an inventory refresh after a scan track conflict: %s", async (detail) => {
    const baseApi = installProductionApi();
    const originalVersion = "a".repeat(64);
    const newVersion = "b".repeat(64);
    const attempts: string[] = [];
    const inventoryReads = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/media/audio-tracks") {
        inventoryReads();
        const response = await baseApi(input, init);
        const inventory = await response.json();
        const version = attempts.length ? newVersion : originalVersion;
        return json({ ...inventory, file_version: version, selection: { ...inventory.selection, file_version: version } });
      }
      if (url.pathname === "/api/scans" && init?.method === "POST") {
        const selection = JSON.parse(String(init.body)).audio_selection;
        attempts.push(selection.file_version);
        return selection.file_version === originalVersion
          ? json({ detail }, 409)
          : json({ ...completeScan(), id: "queued-refresh", scan_id: 9, status: "queued" }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(screen.getByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    const add = within(dialog).getByRole("button", { name: "Add to Queue" });
    await waitFor(() => expect(add).toBeEnabled());
    fireEvent.click(add);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(detail);
    expect(add).toBeDisabled();
    expect(attempts).toEqual([originalVersion]);
    expect(inventoryReads).toHaveBeenCalledOnce();

    fireEvent.click(within(dialog).getByRole("button", { name: "Refresh audio tracks" }));
    await waitFor(() => expect(add).toBeEnabled());
    expect(inventoryReads).toHaveBeenCalledTimes(2);
    fireEvent.click(add);
    expect(await screen.findByText("Scan 9 added to the queue.")).toBeVisible();
    expect(attempts).toEqual([originalVersion, newVersion]);
  });

  it("defaults to recommended models and explains the Severity and Description model", async () => {
    let request: Record<string, unknown> | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/capabilities") return json({
        ...audioCapabilities,
        video_models: [
          { id: "nudenet", display_name: "NudeNet", recommended: false },
          { id: "qwen3-vl-8b", display_name: "Qwen3-VL 8B", recommended: true },
          { id: "qwen3-vl-4b", display_name: "Qwen3-VL 4B", recommended: false },
          { id: "d1-3b", display_name: "d1-3B", recommended: false },
        ],
        qwen_models: [
          { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B", recommended: false },
          { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true },
        ],
      });
      if (url.pathname === "/api/scans" && init?.method === "POST") {
        request = JSON.parse(String(init.body));
        return json({ ...completeScan(), id: "queued-video", scan_id: 10, status: "queued" }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    chooseScanType(dialog, "Video");
    await expectSelectedModel(dialog, "Video model", "Qwen3-VL 8B");
    fireEvent.click(modelPicker(dialog, "Video model"));
    const videoModels = screen.getByRole("listbox", { name: "Video model" });
    expect(within(within(videoModels).getByRole("group", { name: "Recommended" })).getAllByRole("option").map((option) => option.id.split("-option-")[1]))
      .toEqual(["qwen3-vl-8b"]);
    expect(within(within(videoModels).getByRole("group", { name: "Other models" })).getAllByRole("option").map((option) => option.id.split("-option-")[1]))
      .toEqual(["nudenet", "qwen3-vl-4b", "d1-3b"]);
    fireEvent.keyDown(videoModels, { key: "Escape" });
    expect(dialog).toBeVisible();
    await expectSelectedModel(dialog, "Severity and Description model", "Qwen3-VL 8B");
    expect(infoText(dialog, "About the Severity and Description model")).toContain("Describes flagged scenes and rates their severity when the video model doesn't.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add to Queue" }));

    await waitFor(() => expect(request).not.toBeNull());
    expect(request).toMatchObject({ scan_video: true, video_model: "qwen3-vl-8b", qwen_model: "qwen3-vl-8b-instruct-4bit" });
  });

  it.each(["better_profanity", "qwen"])("submits the selected %s filter for a catalog audio scan", async (filter) => {
    let request: Record<string, unknown> | null = null;
    const baseApi = installProductionApi();
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), "http://editarr.test");
      if (url.pathname === "/api/capabilities") return json(audioCapabilities);
      if (url.pathname === "/api/scans" && init?.method === "POST") {
        request = JSON.parse(String(init.body));
        return json({ ...completeScan(), id: "queued-audio", scan_id: 9, status: "queued" }, 201);
      }
      return baseApi(input, init);
    }));
    window.location.hash = "#/library/movies";
    render(<AuthenticationGate><App /></AuthenticationGate>);

    fireEvent.click(await screen.findByRole("button", { name: "Open Example Movie" }));
    await openWorkflowStep("Scans");
    fireEvent.click(await screen.findByRole("button", { name: "New Scan" }));
    const dialog = screen.getByRole("dialog", { name: "New Scan" });
    expect(scanTypeRadio(dialog, "Audio")).toBeChecked();
    expect(infoText(dialog, "About filters")).toMatch(/The Qwen filter checks the transcript/);
    await expectSelectedModel(dialog, "Filter", "better-profanity");
    await chooseModel(dialog, "Filter", filter === "qwen" ? "Qwen" : "better-profanity");
    await chooseModel(dialog, "Severity and Description model", "Qwen3-VL 4B");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Add to Queue" })).toBeEnabled());
    fireEvent.click(within(dialog).getByRole("button", { name: "Add to Queue" }));

    await waitFor(() => expect(request).not.toBeNull());
    expect(request).toMatchObject({
      scan_audio: true,
      scan_video: false,
      audio_filter: filter,
      qwen_model: "qwen3-vl-4b-instruct-4bit",
      audio_selection: { file_version: "a".repeat(64), track: { stream_index: 1, title: "Main" } },
    });
  });
});
