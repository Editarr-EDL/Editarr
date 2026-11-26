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
  track_title: "ryan - Family Review",
};
const review = { id: "review-1", revision: 4, policy_revision: 3, policy_owner_id: 7 } as SavedReview;
const preview = {
  source: forced, cues: [], changed_cues: 1, unchanged_cues: 10, preview_token: "validated-source",
  track_title: inventory.track_title, filename: "Movie.ryan - Family Review.en.forced.srt",
};
const onConflict = async () => {};

afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("uses the selected source and exact review and audio revisions from preview through publication", async () => {
  const other = { filename: "other.srt", label: "alex - Family Review", format: "srt", forced: true };
  const replacement = { ...preview, source: { ...forced, published_filename: "previous.srt" } };
  vi.mocked(api.fetchReviewSubtitleSources).mockResolvedValueOnce({ ...inventory, published_subtitles: [
    other, { filename: "previous.srt", label: "ryan - Older title", forced: true, format: "srt", owned_by_viewer: true },
  ] });
  vi.mocked(api.fetchReviewSubtitleSources).mockReturnValue(new Promise(() => {}));
  vi.mocked(api.previewReviewSubtitles).mockResolvedValue(replacement);
  vi.mocked(api.publishReviewSubtitles).mockResolvedValue({
    source: forced, filename: preview.filename, replaced: true, refresh_pending: true,
  });
  const { result } = renderHook(() => useReviewSubtitles(review, true, onConflict));
  await waitFor(() => expect(result.current.source).toEqual(normal));
  act(() => result.current.chooseSource(forced));
  await act(() => result.current.requestPreview());
  act(() => result.current.prepare());
  expect(result.current.prepared).toEqual(replacement);
  await act(() => result.current.publish());
  const request = { revision: 4, policy_owner_id: 7, policy_revision: 3, source_id: "forced", audio_source_id: "scan-eng" };
  expect(api.previewReviewSubtitles).toHaveBeenCalledWith(review.id, request, expect.any(AbortSignal));
  expect(api.publishReviewSubtitles).toHaveBeenCalledExactlyOnceWith(review.id, { ...request, preview_token: "validated-source" });
  expect(result.current.published).toEqual([
    other, { filename: preview.filename, label: inventory.track_title, format: "srt", forced: true, owned_by_viewer: true },
  ]);
  expect(result.current.publicationStatus?.message).toMatch(/after Jellyfin refreshes/);
});

it("allows a cue preview without preparing publication when the account cannot use a Jellyfin track title", async () => {
  const message = "Link a Jellyfin account with a username before publishing subtitles.";
  const resultPreview = {
    ...preview, source: normal, preview_token: null, track_title: null, filename: null, publication_block_reason: message,
  };
  vi.mocked(api.fetchReviewSubtitleSources).mockResolvedValue({
    ...inventory, track_title: null, publication_block_reason: message,
  });
  vi.mocked(api.previewReviewSubtitles).mockResolvedValue(resultPreview);
  const { result } = renderHook(() => useReviewSubtitles(review, true, onConflict));
  await waitFor(() => expect(result.current.publicationBlockReason).toBe(message));
  expect(result.current.source).toEqual(normal);
  await act(() => result.current.requestPreview());
  act(() => result.current.prepare());
  await act(() => result.current.publish());
  expect(result.current.preview).toMatchObject({ kind: "ready", value: resultPreview });
  expect(result.current.prepared).toBeUndefined();
  expect(api.publishReviewSubtitles).not.toHaveBeenCalled();
});

it("uses a publication block reported during preview while retaining the exact title and cues", async () => {
  const message = "A subtitle already uses this filename.";
  vi.mocked(api.fetchReviewSubtitleSources).mockResolvedValue(inventory);
  vi.mocked(api.previewReviewSubtitles).mockResolvedValue({
    ...preview, source: normal, preview_token: null, filename: null, publication_block_reason: message,
  });
  const { result } = renderHook(() => useReviewSubtitles(review, true, onConflict));
  await waitFor(() => expect(result.current.source).toEqual(normal));
  expect(result.current.publicationBlockReason).toBeUndefined();
  await act(() => result.current.requestPreview());
  expect(result.current.preview?.kind).toBe("ready");
  expect(result.current.publicationBlockReason).toBe(message);
  act(() => result.current.prepare());
  expect(result.current.prepared).toBeUndefined();
  await act(() => result.current.publish());
  expect(api.publishReviewSubtitles).not.toHaveBeenCalled();
});

it("requires a new preview when the server track title changes", async () => {
  const changedTitle = "ryan - Evening Review";
  vi.mocked(api.fetchReviewSubtitleSources)
    .mockResolvedValueOnce(inventory)
    .mockResolvedValue({ ...inventory, track_title: changedTitle });
  vi.mocked(api.previewReviewSubtitles).mockResolvedValue({ ...preview, track_title: changedTitle });
  const { result } = renderHook(() => useReviewSubtitles(review, true, onConflict));
  await waitFor(() => expect(result.current.sources?.track_title).toBe(inventory.track_title));
  await act(() => result.current.requestPreview());
  await waitFor(() => expect(result.current.sources?.track_title).toBe(changedTitle));
  expect(result.current.prepared).toBeUndefined();
  await act(() => result.current.publish());
  expect(api.publishReviewSubtitles).not.toHaveBeenCalled();
  await act(() => result.current.requestPreview());
  expect(result.current.preview?.kind).toBe("ready");
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
