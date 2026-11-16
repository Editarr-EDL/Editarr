import { useEffect, useRef, useState } from "react";

import { fetchSavedReviewMutePreview, fetchSavedReviewVideoPreview } from "../api/api-reviews";
import type { SavedReview } from "../api/api-review-types";
import { isReviewDraftDirty, type ReviewDraft } from "./review-draft";
import { useReviewLifetime } from "./review-access";

/** Own review preview URLs from allocation until replacement or workspace teardown. */
export function useReviewPreview({ selected, draft, setBusy, setError, disabled = false, onDenied }: {
  selected: SavedReview | null;
  draft: ReviewDraft | null;
  setBusy: (busy: boolean) => void;
  setError: (error: string | null) => void;
  disabled?: boolean;
  onDenied?: (reason: unknown) => Promise<void>;
}) {
  const [preview, setPreview] = useState<{ reviewId: string; filterId: string; url: string } | null>(null);
  const ownedUrl = useRef<string | null>(null);
  const lifetime = useRef(0);
  const isCurrent = useReviewLifetime();

  useEffect(() => {
    lifetime.current += 1;
    return () => {
      lifetime.current += 1;
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
      ownedUrl.current = null;
    };
  }, []);

  useEffect(() => {
    if (!preview) return;
    if (
      !selected
      || preview.reviewId !== selected.id
      || !draft
      || isReviewDraftDirty(draft)
      || !draft.filters.some((filter) => filter.id === preview.filterId)
    ) {
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
      ownedUrl.current = null;
      setPreview(null);
    }
  }, [draft, preview, selected]);

  const previewFilter = async (filter: SavedReview["filters"][number]) => {
    if (!selected || !draft || disabled || !isCurrent() || isReviewDraftDirty(draft)) return;
    const request = lifetime.current;
    setBusy(true); setError(null);
    try {
      const blob = filter.edit_action === "mute"
        ? await fetchSavedReviewMutePreview(selected.id, filter.id)
        : await fetchSavedReviewVideoPreview(selected.id, filter.id);
      const url = URL.createObjectURL(blob);
      if (!isCurrent() || request !== lifetime.current) {
        URL.revokeObjectURL(url);
        return;
      }
      if (ownedUrl.current) URL.revokeObjectURL(ownedUrl.current);
      ownedUrl.current = url;
      setPreview({ reviewId: selected.id, filterId: filter.id, url });
    } catch (reason) {
      if (!isCurrent()) return;
      await onDenied?.(reason);
      if (request === lifetime.current) setError(reason instanceof Error ? reason.message : "Unable to render review preview.");
    } finally {
      if (request === lifetime.current) setBusy(false);
    }
  };

  return { preview, previewFilter };
}