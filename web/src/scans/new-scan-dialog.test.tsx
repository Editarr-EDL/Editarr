import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JellyfinMediaVersion } from "../api/api-media-types";
import type { ScanCapabilities } from "../api/api-system-types";
import { NewScanDialog } from "./new-scan-dialog";
import type { NewScanForm } from "./use-new-scan";

const capabilities: ScanCapabilities = {
  audio_models: [{ id: "whisper", display_name: "Whisper" }],
  audio_filters: [{ id: "better_profanity", display_name: "better-profanity" }],
  video_models: [{ id: "nudenet", display_name: "NudeNet" }],
  qwen_models: [{ id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B" }],
  model_capabilities: [],
};
const versions = [{ id: "source-1", name: "4K HDR", resolution: "4K", media_format: "MKV" }] as JellyfinMediaVersion[];

function fakeForm(overrides: Partial<NewScanForm> = {}): NewScanForm {
  const video = { coarseScanEnabled: false, setCoarseScanEnabled: vi.fn(), coarseScanInterval: 5, setCoarseScanInterval: vi.fn(),
    qwenSummariesPerSegment: 4, setQwenSummariesPerSegment: vi.fn(), qwenSegmentJoinGapSeconds: 3,
    setQwenSegmentJoinGapSeconds: vi.fn(), invalid: false };
  return {
    open: true, openForm: vi.fn(), close: vi.fn(), submit: vi.fn(), capabilities, adding: false, submitError: null, clearError: vi.fn(),
    scanType: "combined", setScanType: vi.fn(), scanAudio: true, scanVideo: true,
    audioTracks: { inventory: null, selection: null, loading: false, error: null, source: "", ready: false,
      onRetry: vi.fn(), onChange: vi.fn(), prepare: vi.fn() },
    needsAudioTrackRefresh: false, refreshAudioTracks: vi.fn(),
    audioModel: "whisper", setAudioModel: vi.fn(), audioFilter: "better_profanity", setAudioFilter: vi.fn(),
    videoModel: "nudenet", setVideoModel: vi.fn(), qwenModel: "qwen3-vl-8b-instruct-4bit", setQwenModel: vi.fn(),
    video, canSubmit: true, setupLocked: false, ...overrides,
  } as NewScanForm;
}

function renderDialog(form: NewScanForm) {
  render(<NewScanDialog form={form} title="Example Movie" meta="Movie · 2025" versions={versions}
    mediaSourceId="source-1" onVersionChange={vi.fn()} />);
  return screen.getByRole("dialog", { name: "New Scan" });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("NewScanDialog", () => {
  it("traps Tab on the checked scan type and visible controls, starting at Version", async () => {
    const dialog = renderDialog(fakeForm());
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveFocus());
    const visited: Element[] = [document.activeElement!];
    for (let step = 0; step < 30; step++) {
      fireEvent.keyDown(document.activeElement!, { key: "Tab" });
      if (visited.includes(document.activeElement!)) break;
      visited.push(document.activeElement!);
    }
    const names = visited.map((element) => element.getAttribute("aria-label") || element.textContent?.trim());
    expect(names.slice(0, 3)).toEqual(["Version", "Close", "Audio and video"]);
    expect(names).toContain("Change Transcriber: Whisper");
    expect(names).not.toContain("Audio");
    expect(visited.some((element) => element.tagName === "SUMMARY")).toBe(true);
    expect(names).not.toContain("Representative frames per flagged segment");
    expect(names.at(-1)).toBe("Add to Queue");
  });

  it("leaves Escape to an open model list, then closes unless a scan is being added", async () => {
    const form = fakeForm();
    const dialog = renderDialog(form);
    fireEvent.click(within(dialog).getByRole("button", { name: "Change Video model: NudeNet" }));
    fireEvent.keyDown(screen.getByRole("listbox", { name: "Video model" }), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(form.close).not.toHaveBeenCalled();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(form.close).toHaveBeenCalledOnce();

    cleanup();
    const busy = fakeForm({ adding: true, canSubmit: false });
    const busyDialog = renderDialog(busy);
    fireEvent.keyDown(busyDialog, { key: "Escape" });
    expect(busy.close).not.toHaveBeenCalled();
    expect(within(busyDialog).getByRole("button", { name: "Close" })).toBeDisabled();
    expect(within(busyDialog).getByRole("button", { name: "Adding…" })).toBeDisabled();
  });

  it("summarizes the selected run and submits the typed scan name", async () => {
    const form = fakeForm();
    const dialog = renderDialog(form);
    expect(dialog).toHaveTextContent("Will runWhisper → then better-profanity · NudeNet");
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Scan name (optional)" }), { target: { value: "Director's cut" } });
    await act(async () => { fireEvent.click(within(dialog).getByRole("button", { name: "Add to Queue" })); });
    expect(form.submit).toHaveBeenCalledWith("Director's cut");
  });

  it("updates the Qwen filter's download size when the description model changes", () => {
    const qwenCapabilities: ScanCapabilities = {
      ...capabilities,
      audio_filters: [{ id: "qwen", display_name: "Qwen (multilingual)" }],
      qwen_models: [{ id: "qwen3-vl-8b-instruct-4bit", display_name: "Qwen3-VL 8B", recommended: true },
        { id: "qwen3-vl-4b-instruct-4bit", display_name: "Qwen3-VL 4B" }],
      model_capabilities: (["qwen", "qwen3-vl-8b", "qwen3-vl-4b"] as const).map((id) => ({
        id, display_name: id, stage: id === "qwen" ? "audio" : "detector", availability: "available", reason: null, device: "mlx",
        weight_requirement: "on_demand", enrichment_behavior: "none",
        download: id === "qwen3-vl-4b"
          ? { state: "not_downloaded", bytes_downloaded: null, bytes_total: 3_109_729_929, error_code: null, error_detail: null }
          : { state: "ready", bytes_downloaded: null, bytes_total: 5_776_630_909, error_code: null, error_detail: null },
      })),
    };
    const form = fakeForm({ capabilities: qwenCapabilities, audioFilter: "qwen" });
    const filterCard = () => screen.getByRole("button", { name: /Filter: Qwen$/ }).closest(".model-picker")!;
    const { rerender } = render(<NewScanDialog form={form} title="Example Movie" meta="Movie · 2025" versions={versions}
      mediaSourceId="source-1" onVersionChange={vi.fn()} />);
    expect(filterCard()).toHaveTextContent("Ready · 5.4 GB");
    rerender(<NewScanDialog form={{ ...form, qwenModel: "qwen3-vl-4b-instruct-4bit" }} title="Example Movie" meta="Movie · 2025"
      versions={versions} mediaSourceId="source-1" onVersionChange={vi.fn()} />);
    expect(filterCard()).toHaveTextContent("Downloads 2.9 GB first");
  });
});
