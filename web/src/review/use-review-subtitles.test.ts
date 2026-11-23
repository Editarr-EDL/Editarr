import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import * as api from "../api/api-review-subtitles";
import type { ReviewSubtitleSources, SubtitleSource } from "../api/api-review-subtitles";
import type { SavedReview } from "../api/api-review-types";
import { useReviewSubtitles } from "./use-review-subtitles";

vi.mock("../api/api-review-subtitles", () => ({
  fetchPublishedSubtitles: vi.fn(), fetchReviewSubtitleSources: vi.fn(), previewReviewSubtitles: vi.fn(), publishReviewSubtitles: vi.fn(),
}));

const normal: SubtitleSource = {
  id: "normal", label: "English", language: "eng", format: "srt",
  kind: "external", forced: false, sdh: false, supported: true, recommended: true,
};
const forced: SubtitleSource = {
  ...normal, id: "forced", label: "English Forced", kind: "embedded", forced: true, recommended: false,
};
const inventory: ReviewSubtitleSources = {
  sources: [normal, forced], audio_sources: [{ id: "scan-eng", label: "English", language: "eng" }],
  audio_source_id: "scan-eng", fallback: null, published_subtitles: [],
};
const review = { id: "review-1", revision: 4, policy_revision: 3, policy_owner_id: 7 } as SavedReview;
const preview = { source: forced, cues: [], changed_cues: 1, unchanged_cues: 10, preview_token: "validated-source" };
const onConflict = async () => {};

afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("uses the selected source and exact review and audio revisions from preview through publication", async () => {
  vi.mocked(api.fetchReviewSubtitleSources).mockResolvedValue({ ...inventory, published_subtitles: [{
    filename: "previous.srt", label: "English Forced", forced: true, format: "srt",
  }] });
  vi.mocked(api.previewReviewSubtitles).mockResolvedValue(preview);
  vi.mocked(api.publishReviewSubtitles).mockResolvedValue({
    source: forced, filename: "previous.srt", replaced: true, refresh_pending: true,
  });
  const { result } = renderHook(() => useReviewSubtitles(review, true, onConflict));
  await waitFor(() => expect(result.current.source).toEqual(normal));
  act(() => result.current.chooseSource(forced));
  await act(() => result.current.requestPreview());
  act(() => result.current.prepare());
  expect(result.current.prepared).toEqual(forced);
  await act(() => result.current.publish());
  const request = { revision: 4, policy_owner_id: 7, policy_revision: 3, source_id: "forced", audio_source_id: "scan-eng" };
  expect(api.previewReviewSubtitles).toHaveBeenCalledWith(review.id, request, expect.any(AbortSignal));
  expect(api.publishReviewSubtitles).toHaveBeenCalledExactlyOnceWith(review.id, { ...request, preview_token: "validated-source" });
  expect(result.current.published).toHaveLength(1);
  expect(result.current.publicationStatus?.message).toMatch(/after Jellyfin refreshes/);
});

it("discards a late preview when the user chooses a different saved review", async () => {
  vi.mocked(api.fetchReviewSubtitleSources).mockResolvedValue(inventory);
  let finish!: (value: typeof preview) => void;
  vi.mocked(api.previewReviewSubtitles).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const { result, rerender } = renderHook(({ selected }) => useReviewSubtitles(selected, true, onConflict),
    { initialProps: { selected: review } });
  await waitFor(() => expect(result.current.source).toEqual(normal));
  act(() => result.current.chooseSource(forced));
  act(() => { void result.current.requestPreview(); });
  expect(result.current.previewing).toBe(true);
  rerender({ selected: { ...review, id: "review-2" } });
  await act(async () => finish(preview));
  expect(result.current.prepared).toBeUndefined();
  expect(result.current.preview).toBeUndefined();
  await waitFor(() => expect(result.current.source).toEqual(normal));
  await act(() => result.current.publish());
  expect(api.publishReviewSubtitles).not.toHaveBeenCalled();
});
