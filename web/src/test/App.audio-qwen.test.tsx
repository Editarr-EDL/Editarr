import { openWorkflowStep } from "./review-workflow-test-helpers";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { AuthenticationGate } from "../auth/auth-gate";
import { completeScan, installProductionApi, json, mediaItem, signedInStatus } from "./app-test-fixtures";

const audioCapabilities = {
  audio_models: [{ id: "whisper", display_name: "Whisper" }],
  audio_filters: [
    { id: "better_profanity", display_name: "better-profanity" },
    { id: "qwen", display_name: "Qwen (multilingual)" },
  ],
  video_models: [{ id: "nudenet", display_name: "NudeNet" }],
  qwen_models: [
    { id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B" },
    { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B" },
  ],
  model_capabilities: [],
};

describe("Qwen language scan controls", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it.each(["audio", "video", "combined"])("submits a path-free catalog %s scan with its selected options", async (mode) => {
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
    expect(within(dialog).getByLabelText("Scan type")).toHaveValue("audio");
    fireEvent.change(within(dialog).getByLabelText("Scan type"), { target: { value: mode } });
    if (mode !== "video") {
      await within(dialog).findByRole("option", { name: "Qwen (multilingual)" });
      fireEvent.change(within(dialog).getByLabelText("Filter"), { target: { value: "qwen" } });
    }
    if (mode !== "audio") {
      fireEvent.change(within(dialog).getByLabelText("Representative frames per flagged segment"), { target: { value: "6" } });
      fireEvent.change(within(dialog).getByLabelText("Join flagged segments within (seconds)"), { target: { value: "2" } });
    }
    await within(dialog).findByRole("option", { name: "Qwen3-VL 4B" });
    fireEvent.change(within(dialog).getByLabelText("Severity and Description model"), {
      target: { value: "qwen3-vl-4b-instruct-4bit" },
    });
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
      audio_filter: mode === "video" ? null : "qwen",
      qwen_model: "qwen3-vl-4b-instruct-4bit",
    });
    if (mode !== "video") expect(request).toMatchObject({ audio_selection: { file_version: "a".repeat(64), track: { stream_index: 1, title: "Main" } } });
    if (mode !== "audio") expect(request).toMatchObject({ video_model: "nudenet", qwen_summaries_per_segment: 6, qwen_segment_join_gap_seconds: 2 });
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("path-mappings"))).toBe(false);
    expect(vi.mocked(fetch).mock.calls.some(([input]) => /\/models(?:[/?]|$)|\/settings\//.test(String(input)))).toBe(false);
    expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input).includes("audio-tracks")).every(([input, init]) => !String(input).includes("video_path") && !String(init?.body).includes("video_path"))).toBe(true);
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
    fireEvent.change(within(dialog).getByLabelText("Scan type"), { target: { value: "video" } });
    const videoModel = within(dialog).getByLabelText("Video model");
    await waitFor(() => expect(videoModel).toHaveValue("qwen3-vl-8b"));
    expect(within(videoModel).getAllByRole("option").map((option) => option.textContent)).toEqual(["NudeNet", "Qwen3-VL 8B (recommended)", "Qwen3-VL 4B", "d1-3B"]);
    const severityModel = within(dialog).getByRole("combobox", { name: "Severity and Description model" });
    expect(severityModel).toHaveValue("qwen3-vl-8b-instruct-4bit");
    expect(within(severityModel).getByRole("option", { name: "Qwen3-VL 8B (recommended)" })).toBeInTheDocument();
    expect(severityModel).toHaveAccessibleDescription("Describes flagged scenes and rates their severity when the video model doesn't.");
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
    expect(within(dialog).getByLabelText("Scan type")).toHaveValue("audio");
    expect(within(dialog).getByText(/The Qwen filter checks the transcript/)).toBeVisible();
    await within(dialog).findByRole("option", { name: "Qwen (multilingual)" });
    expect(within(dialog).getByLabelText("Filter")).toHaveValue("better_profanity");
    fireEvent.change(within(dialog).getByLabelText("Filter"), { target: { value: filter } });
    await within(dialog).findByRole("option", { name: "Qwen3-VL 4B" });
    fireEvent.change(within(dialog).getByLabelText("Severity and Description model"), {
      target: { value: "qwen3-vl-4b-instruct-4bit" },
    });
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
