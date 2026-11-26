import { useCallback, useEffect, useRef, useState } from "react";
import { fetchDraftFilterPreview, type DraftFilterPreview } from "../api/api-reviews";
import type { ReviewTargetRequest } from "../api/api-review-types";

export type FilterClip<Key = string> = { key: Key; url: string; kind: "audio" | "video" };

/** Preview request for a filter range: mute filters preview audio, other actions preview video. */
export function rangePreview(range: { startMs: number; endMs: number; action: string }): DraftFilterPreview {
  const audio = range.action === "mute";
  return { start_seconds: range.startMs / 1000, end_seconds: range.endMs / 1000,
    source: audio ? "audio" : "visual", edit_action: audio ? "mute" : "skip" };
}

/**
 * Load the review clip preview for a filter on the target file. Only the latest request's clip is
 * kept, earlier requests are aborted, and object URLs are revoked when replaced, reset or unmounted.
 */
export function useFilterClip<Key = string>(target: ReviewTargetRequest) {
  const [clip, setClip] = useState<FilterClip<Key> | null>(null);
  const [loading, setLoading] = useState<Key | null>(null);
  const [error, setError] = useState<string | null>(null);
  const url = useRef<string | null>(null);
  const pending = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    pending.current?.abort();
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = null;
    setClip(null);
    setLoading(null);
    setError(null);
  }, []);
  useEffect(() => () => {
    pending.current?.abort();
    if (url.current) URL.revokeObjectURL(url.current);
  }, []);

  const play = async (key: Key, preview: DraftFilterPreview) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setLoading(key);
    setError(null);
    try {
      const blob = await fetchDraftFilterPreview(target, preview, controller.signal);
      if (controller.signal.aborted) return;
      if (url.current) URL.revokeObjectURL(url.current);
      url.current = URL.createObjectURL(blob);
      setClip({ key, url: url.current, kind: preview.source === "audio" ? "audio" : "video" });
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to preview the clip.");
    } finally { if (!controller.signal.aborted) setLoading(null); }
  };
  return { clip, loading, error, play, reset };
}

export function FilterClipPlayer({ clip, error }: { clip: FilterClip<unknown> | null; error: string | null }) {
  return <>
    {error && <p className="form-error" role="alert">{error}</p>}
    {clip?.kind === "audio" && <audio controls tabIndex={0} preload="metadata" aria-label="Filter clip preview" src={clip.url} />}
    {clip?.kind === "video" && <video controls tabIndex={0} preload="metadata" aria-label="Filter clip preview" src={clip.url} />}
  </>;
}
