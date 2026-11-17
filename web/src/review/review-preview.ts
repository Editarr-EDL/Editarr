import { useCallback, useEffect, useRef, useState } from "react";

import { fetchDraftFilterPreview, fetchSavedReviewMutePreview, fetchSavedReviewVideoPreview } from "../api/api-reviews";
import type { ReviewFilter, ReviewTargetRequest, SavedReview } from "../api/api-review-types";
import type { ReviewDraft } from "./review-draft";
import { useReviewLifetime } from "./review-access";

export type PreviewFilter = Pick<ReviewFilter, "id" | "source" | "edit_action" | "start_seconds" | "end_seconds">
  & Partial<Pick<ReviewFilter, "source_scan_id" | "source_finding_id">>;

function sameClip(saved: ReviewFilter, draft: PreviewFilter): boolean {
  return saved.source_scan_id === draft.source_scan_id && saved.source_finding_id === draft.source_finding_id
    && saved.source === draft.source && saved.edit_action === draft.edit_action
    && saved.start_seconds === draft.start_seconds && saved.end_seconds === draft.end_seconds;
}

/** Own preview URLs from allocation until the draft changes or the workspace closes. */
export function useReviewPreview({ target, selected, draft, setBusy, setError, disabled = false, onDenied }: {
  target: ReviewTargetRequest;
  selected: SavedReview | null;
  draft: ReviewDraft | null;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  disabled?: boolean;
  onDenied?: (reason: unknown) => Promise<void>;
}) {
  const [preview, setPreview] = useState<{ draft: ReviewDraft; filterId: string; url: string } | null>(null);
  const ownedUrl = useRef<string | null>(null);
  const lifetime = useRef(0);
  const previewRequest = useRef(0);
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const isCurrent = useReviewLifetime();

  const revoke = useCallback(() => {
    if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
    ownedUrl.current = null;
  }, []);
  const clearPreview = useCallback(() => {
    previewRequest.current += 1;
    revoke();
    setPreview(null);
  }, [revoke]);

  useEffect(() => {
    lifetime.current += 1;
    return () => {
      lifetime.current += 1;
      revoke();
    };
  }, [revoke]);

  useEffect(() => {
    if (preview && preview.draft !== draft) clearPreview();
  }, [draft, preview, clearPreview]);

  const previewFilter = async (filter: PreviewFilter) => {
    if (!draft || disabled || !isCurrent()) return;
    const request = ++previewRequest.current;
    const generation = lifetime.current;
    const stillRequested = () => isCurrent() && request === previewRequest.current
      && generation === lifetime.current && currentDraft.current === draft;
    setBusy(true); setError(null);
    try {
      const saved = selected?.filters.find((candidate) => candidate.id === filter.id);
      const blob = selected && saved && sameClip(saved, filter)
        ? await (filter.edit_action === "mute"
          ? fetchSavedReviewMutePreview(selected.id, filter.id)
          : fetchSavedReviewVideoPreview(selected.id, filter.id))
        : await fetchDraftFilterPreview(target, {
          review_id: selected?.id,
          source_scan_id: filter.source_scan_id,
          source_finding_id: filter.source_finding_id,
          start_seconds: filter.start_seconds,
          end_seconds: filter.end_seconds,
          edit_action: filter.edit_action,
          source: filter.source,
        });
      const url = URL.createObjectURL(blob);
      if (!stillRequested()) {
        URL.revokeObjectURL(url);
        return;
      }
      revoke();
      ownedUrl.current = url;
      setPreview({ draft, filterId: filter.id, url });
    } catch (reason) {
      if (!stillRequested()) return;
      await onDenied?.(reason);
      if (stillRequested()) setError(reason instanceof Error ? reason.message : "Unable to render review preview.");
    } finally {
      if (generation === lifetime.current) setBusy(false);
    }
  };

  return { preview: preview?.draft === draft ? preview : null, previewFilter, clearPreview };
}
