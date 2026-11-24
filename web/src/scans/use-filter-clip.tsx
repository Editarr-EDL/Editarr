import { useEffect, useRef, useState } from "react";
import { fetchDraftFilterPreview } from "../api/api-reviews";
import type { MasterTarget } from "../api/api-community-master-types";

export type FilterClip = { key: string; url: string; kind: "audio" | "video" };

/**
 * Load the existing review clip preview for a filter range on the local file. Mute filters preview
 * the audio track; other actions preview video. Only the latest request's clip is kept.
 */
export function useFilterClip(target: MasterTarget) {
  const [clip, setClip] = useState<FilterClip | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const url = useRef<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => {
    pending.current?.abort();
    if (url.current) URL.revokeObjectURL(url.current);
  }, []);

  const play = async (key: string, range: { startMs: number; endMs: number; action: string }) => {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const audio = range.action === "mute";
    setLoading(key);
    setError(null);
    try {
      const blob = await fetchDraftFilterPreview(target, {
        start_seconds: range.startMs / 1000, end_seconds: range.endMs / 1000,
        source: audio ? "audio" : "visual", edit_action: audio ? "mute" : "skip",
      }, controller.signal);
      if (controller.signal.aborted) return;
      if (url.current) URL.revokeObjectURL(url.current);
      url.current = URL.createObjectURL(blob);
      setClip({ key, url: url.current, kind: audio ? "audio" : "video" });
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to preview the clip.");
    } finally { if (!controller.signal.aborted) setLoading(null); }
  };
  return { clip, loading, error, play };
}

export function FilterClipPlayer({ clip, error }: { clip: FilterClip | null; error: string | null }) {
  return <>
    {error && <p className="form-error" role="alert">{error}</p>}
    {clip?.kind === "audio" && <audio controls tabIndex={0} preload="metadata" aria-label="Filter clip preview" src={clip.url} />}
    {clip?.kind === "video" && <video controls tabIndex={0} preload="metadata" aria-label="Filter clip preview" src={clip.url} />}
  </>;
}
