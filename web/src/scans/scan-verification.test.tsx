import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { fetchDraftFilterPreview } from "../api/api-reviews";
import { previewScanReuse } from "../api/api-stable-media";
import { completeScan } from "../test/app-test-fixtures";
import { ScanVerificationDialog } from "./scan-verification";

vi.mock("../api/api-reviews", () => ({ fetchDraftFilterPreview: vi.fn() }));
vi.mock("../api/api-stable-media", () => ({ previewScanReuse: vi.fn() }));
vi.mock("./use-audio-tracks", () => ({
  useAudioTracks: () => ({ ready: true, selection: null, prepare: async () => ({ track: { stream_index: 1 } }) }),
}));
vi.mock("./use-verification-transcript", () => ({
  useVerificationTranscript: () => ({ status: "ready", markMissing: vi.fn() }),
}));

it("requires playback of the video for a cue that affects audio and video", async () => {
  vi.mocked(previewScanReuse).mockResolvedValue({
    version_id: "target-version", source_scan_id: "combined", token: "token", exact: false,
    verified_channels: ["audio", "video"], anchors: [], issues: [],
    cues: [{ source_finding_id: 1, channel: "both", source_start: 10, source_end: 12, target_start: 11, target_end: 13 }],
  });
  vi.mocked(fetchDraftFilterPreview).mockResolvedValue(new Blob());
  const objectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
  try {
    render(<ScanVerificationDialog
      scan={completeScan({ id: "combined", scan_audio: true, scan_video: true })}
      originalName="Earlier version" itemName="Movie" targetName="Current version"
      target={{ source: "jellyfin", item_id: "item", media_source_id: "source-1" }}
      currentScans={[]} submittedAudioScan={null} mayStartAudioScan={false}
      onStartAudioScan={vi.fn()} onClose={vi.fn()} onApproved={vi.fn()} onApprovalError={vi.fn()}
    />);
    fireEvent.click(screen.getByRole("button", { name: "Preview alignment" }));
    const approve = await screen.findByRole("button", { name: "Continue to approval" });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Preview at 00:11.000" }));
    const clip = await screen.findByLabelText("Target cue preview");
    expect(clip.tagName).toBe("VIDEO");
    expect(fetchDraftFilterPreview).toHaveBeenCalledWith(expect.anything(),
      expect.objectContaining({ source: "visual", edit_action: "skip", start_seconds: 11 }), expect.anything());
    fireEvent.play(clip);
    expect(approve).toBeDisabled();
    fireEvent.playing(clip);
    await waitFor(() => expect(approve).toBeEnabled());
  } finally {
    objectURL.mockRestore();
  }
});
