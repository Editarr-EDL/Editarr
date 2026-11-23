import { useEffect, useRef, useState } from "react";
import type { ReviewTargetRequest, SavedReview } from "../api/api-review-types";
import { reviewPolicyToken } from "../api/api-reviews";
import {
  fetchPublishedSubtitles, fetchReviewSubtitleSources, previewReviewSubtitles, publishReviewSubtitles,
  type PublishedSubtitle, type ReviewSubtitlePreview, type ReviewSubtitleSources, type SubtitleSource,
} from "../api/api-review-subtitles";
import { ApiError } from "../api/api-transport";
import { useReviewLifetime } from "./review-access";

type LoadState<T> =
  | { key: string; kind: "ready"; value: T }
  | { key: string; kind: "error"; message: string };

const errorText = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

function isReviewConflict(reason: unknown): boolean {
  return reason instanceof ApiError && ["stale_review_revision", "stale_review_policy"].includes(reason.code ?? "");
}

/** Bind track choices and previews to the exact saved revision and reviewed audio track. */
export function useReviewSubtitles(
  review: SavedReview | null,
  canExport: boolean,
  onConflict: (reason: unknown) => Promise<void>,
  { target, discover = true }: { target?: ReviewTargetRequest; discover?: boolean } = {},
) {
  const id = canExport && !review?.repair ? review?.id : undefined;
  const revision = review?.revision;
  const policyRevision = review?.policy_revision;
  const policyOwner = review?.policy_owner_id;
  const reviewKey = JSON.stringify([id, revision, policyRevision, policyOwner]);
  const [audio, setAudio] = useState<{ key: string; id: string } | null>(null);
  const audioId = audio?.key === reviewKey ? audio.id : undefined;
  const inventoryKey = JSON.stringify([reviewKey, audioId]);
  const [inventory, setInventory] = useState<LoadState<ReviewSubtitleSources>>();
  const [reload, setReload] = useState(0);
  const [published, setPublished] = useState<PublishedSubtitle[]>([]);
  const [selection, setSelection] = useState<{ key: string; id: string }>();
  const [previewState, setPreviewState] = useState<LoadState<ReviewSubtitlePreview>>();
  const [previewing, setPreviewing] = useState<string>();
  const [preparedKey, setPreparedKey] = useState<string>();
  const [publishing, setPublishing] = useState(false);
  const [publicationStatus, setPublicationStatus] = useState<{ key: string; error: boolean; message: string }>();
  const previewRequest = useRef<AbortController | null>(null);
  const runningPublication = useRef(false);
  const current = useReviewLifetime();
  const lifetime = useRef(current);
  lifetime.current = current;
  const conflictHandler = useRef(onConflict);
  conflictHandler.current = onConflict;

  const inventoryState = inventory?.key === inventoryKey ? inventory : undefined;
  const sources = inventoryState?.kind === "ready" ? inventoryState.value : undefined;
  const source = sources?.sources.find((candidate) => candidate.supported && candidate.id === selection?.id && selection.key === inventoryKey)
    ?? sources?.sources.find((candidate) => candidate.supported && candidate.recommended)
    ?? sources?.sources.find((candidate) => candidate.supported && candidate.kind === "transcript");
  const confirmedAudio = sources?.audio_source_id ?? audioId;
  const sourceKey = JSON.stringify([inventoryKey, confirmedAudio, source?.id]);
  const activeSourceKey = useRef(sourceKey);
  activeSourceKey.current = sourceKey;
  const targetSource = target?.source;
  const targetItemId = target?.item_id;
  const targetMediaSourceId = target?.media_source_id;

  useEffect(() => {
    if (!targetSource || !targetItemId) return;
    const controller = new AbortController();
    void fetchPublishedSubtitles({
      source: targetSource, item_id: targetItemId, media_source_id: targetMediaSourceId,
    }, controller.signal).then((result) => {
      if (!controller.signal.aborted && lifetime.current()) setPublished(result);
    }).catch(() => {});
    return () => controller.abort();
  }, [targetSource, targetItemId, targetMediaSourceId, reload]);

  useEffect(() => {
    if (!discover || !id || revision == null) return;
    const controller = new AbortController();
    const request = {
      revision, policy_revision: policyRevision, policy_owner_id: policyOwner,
      ...(audioId ? { audio_source_id: audioId } : {}),
    };
    void fetchReviewSubtitleSources(id, request, controller.signal).then((result) => {
      if (controller.signal.aborted || !lifetime.current()) return;
      setInventory({ key: inventoryKey, kind: "ready", value: result });
      if (!targetItemId) setPublished(result.published_subtitles);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted || !lifetime.current()) return;
      setInventory({ key: inventoryKey, kind: "error", message: errorText(reason, "Unable to find subtitle tracks.") });
      if (isReviewConflict(reason)) void conflictHandler.current(reason);
    });
    return () => controller.abort();
  }, [discover, id, revision, policyRevision, policyOwner, audioId, inventoryKey, reload, targetItemId]);

  useEffect(() => () => { previewRequest.current?.abort(); }, [sourceKey]);

  const preview = previewState?.key === sourceKey ? previewState : undefined;
  const request = source && review && id ? {
    revision: review.revision, ...reviewPolicyToken(review), source_id: source.id,
    ...(confirmedAudio ? { audio_source_id: confirmedAudio } : {}),
  } : null;

  async function requestPreview() {
    if (!request || !id) return;
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    setPreviewing(sourceKey);
    setPreviewState(undefined);
    setPreparedKey(undefined);
    try {
      const result = await previewReviewSubtitles(id, request, controller.signal);
      if (!controller.signal.aborted && current() && activeSourceKey.current === sourceKey) {
        setPreviewState({ key: sourceKey, kind: "ready", value: result });
      }
    } catch (reason) {
      if (controller.signal.aborted || !current() || activeSourceKey.current !== sourceKey) return;
      setPreviewState({ key: sourceKey, kind: "error", message: errorText(reason, "Unable to preview filtered subtitles.") });
      if (isReviewConflict(reason)) await conflictHandler.current(reason);
    } finally {
      if (previewRequest.current === controller) {
        previewRequest.current = null;
        if (current()) setPreviewing(undefined);
      }
    }
  }

  async function publish() {
    if (!request || !id || preparedKey !== sourceKey || preview?.kind !== "ready" || runningPublication.current) return;
    runningPublication.current = true;
    setPublishing(true);
    setPublicationStatus(undefined);
    try {
      const result = await publishReviewSubtitles(id, { ...request, preview_token: preview.value.preview_token });
      if (!current()) return;
      setPublished((previous) => [
        ...previous.filter((track) => track.filename !== result.filename),
        { filename: result.filename, label: `${result.source.label} · Filtered`, format: result.source.format, forced: result.source.forced },
      ]);
      setReload((value) => value + 1);
      const message = result.refresh_pending
        ? "Saved the filtered subtitle. It will appear after Jellyfin refreshes this item."
        : result.replaced ? "Replaced the previous filtered subtitle track and requested a Jellyfin refresh."
          : "Saved the filtered subtitle track and requested a Jellyfin refresh.";
      setPublicationStatus({ key: sourceKey, error: false, message });
    } catch (reason) {
      if (!current() || activeSourceKey.current !== sourceKey) return;
      setPublicationStatus({ key: sourceKey, error: true, message: errorText(reason, "Unable to publish filtered subtitles.") });
      if (reason instanceof ApiError && ["subtitle_source_changed", "subtitle_media_changed"].includes(reason.code ?? "")) {
        setPreparedKey(undefined);
        setPreviewState(undefined);
        setReload((value) => value + 1);
      }
      if (isReviewConflict(reason)) await conflictHandler.current(reason);
    } finally {
      runningPublication.current = false;
      if (current()) setPublishing(false);
    }
  }

  return {
    sources,
    inventoryError: inventoryState?.kind === "error" ? inventoryState.message : undefined,
    reload: () => { setInventory(undefined); setReload((value) => value + 1); },
    audioId: confirmedAudio,
    chooseAudio: (next: string) => { setAudio(next ? { key: reviewKey, id: next } : null); },
    chooseSource: (next: SubtitleSource) => { setSelection({ key: inventoryKey, id: next.id }); },
    source,
    preview,
    previewing: previewing === sourceKey,
    requestPreview,
    prepare: () => { if (preview?.kind === "ready") setPreparedKey(sourceKey); },
    prepared: preparedKey === sourceKey ? source : undefined,
    published,
    publishing,
    publish,
    publicationStatus: publicationStatus?.key === sourceKey ? publicationStatus : undefined,
  };
}

export type ReviewSubtitles = ReturnType<typeof useReviewSubtitles>;
