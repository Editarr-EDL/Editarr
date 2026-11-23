import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import type { AudioTrackSelection } from "../api/api-audio-tracks";
import type { ScanQueueItem } from "../api/api-scan-types";
import { fetchVerificationTranscriptStatus, type VerificationRequest } from "../api/api-stable-media";
import { completeScan } from "../test/app-test-fixtures";
import { useVerificationTranscript } from "./use-verification-transcript";

vi.mock("../api/api-stable-media", () => ({ fetchVerificationTranscriptStatus: vi.fn() }));

const target: VerificationRequest["target"] = { source: "jellyfin", item_id: "movie-1", media_source_id: "source-1" };
const selection: AudioTrackSelection = {
  file_version: "a".repeat(64),
  track: { stream_index: 1, language: "eng", title: "Main", codec: "aac", channels: 2, disposition: 1,
    start_seconds: 0, duration_seconds: 120, time_base: "1/48000" },
};
const running: ScanQueueItem = {
  ...completeScan({ id: "target-audio", status: "scanning", scan_audio: true, requested_audio_selection: selection }),
  queue_id: 3, sort_order: 0, progress_percent: null, progress_stage: null, progress_message: null,
};

beforeEach(() => vi.mocked(fetchVerificationTranscriptStatus).mockReset());

it.each(["complete", "failed", "error", "cancelled"])("does not wait on a %s Queue row", async (status) => {
  const complete = status === "complete";
  vi.mocked(fetchVerificationTranscriptStatus).mockResolvedValue({ ready: complete });
  const { result, rerender } = renderHook(({ latest }: { latest: ScanQueueItem }) =>
    useVerificationTranscript({ source: "original", target, selection, channels: ["audio"], scans: [latest], submittedScan: running }),
  { initialProps: { latest: running } });
  expect(result.current.active?.id).toBe(running.id);
  expect(fetchVerificationTranscriptStatus).not.toHaveBeenCalled();

  const terminal = { ...running, status, error_message: complete ? null : "Scan stopped." };
  rerender({ latest: terminal });

  expect(result.current.active).toBeUndefined();
  await waitFor(() => expect(result.current.status).toBe(complete ? "ready" : "missing"));
  expect(result.current.failure).toBe(terminal.error_message);
});

it("keeps the locally resumable orphaned scan visible in Queue", () => {
  const orphaned = { ...running, status: "orphaned" };
  const { result } = renderHook(() =>
    useVerificationTranscript({ source: "original", target, selection, channels: ["audio"], scans: [orphaned], submittedScan: null }));
  expect(result.current.active).toEqual(orphaned);
  expect(fetchVerificationTranscriptStatus).not.toHaveBeenCalled();
});
