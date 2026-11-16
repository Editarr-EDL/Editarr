import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { ReviewMcfDocument, ReviewRepairSummary, SavedReview, SavedReviewSummary } from "../api/api-review-types";
import { fetchSavedReview, fetchSavedReviews } from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { createReviewDraft, isReviewDraftDirty } from "./review-draft";
import type { SavedReviewPanelProps } from "./review-workspace-state";
import { useReviewLifetime } from "./review-access";

const reviewConflictOutcomes = {
  stale_review_revision: "stale",
  stale_review_policy: "stale",
  review_policy_enforced: "refused",
  invalid_review_filter: "refused",
  invalid_membership: "refused",
  review_policy_timing_precision: "refused",
  media_runtime_unavailable: "refused",
  media_unavailable: "refused",
  review_policy_evidence_missing: "refused",
  published_review_retention_required: "refused",
  mcf_metadata_source_required: "refused",
} as const satisfies Record<string, "stale" | "refused">;

function reviewConflictOutcome(code: string | undefined): "stale" | "refused" {
  return code && Object.prototype.hasOwnProperty.call(reviewConflictOutcomes, code)
    ? reviewConflictOutcomes[code as keyof typeof reviewConflictOutcomes]
    : "refused";
}

export function useReviewSelection({ item, mediaSourceId, importedReview, sort, direction,
  catalogMcfDocument, setBusy, setError, setEditingReviewName, resetMetadata,
}: Pick<SavedReviewPanelProps, "item" | "mediaSourceId" | "importedReview"> & {
  sort: "name" | "updated_at";
  direction: "asc" | "desc";
  catalogMcfDocument: () => ReviewMcfDocument;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setEditingReviewName: Dispatch<SetStateAction<boolean>>;
  resetMetadata: () => void;
}) {
  const [reviews, setReviews] = useState<SavedReviewSummary[]>([]);
  const [selected, setSelected] = useState<SavedReview | null>(null);
  const [draft, setDraft] = useState<ReturnType<typeof createReviewDraft> | null>(null);
  const [conflict, setConflict] = useState<"loading" | "ready" | "failed" | null>(null);
  const reviewLoadRequest = useRef(0);
  const isCurrent = useReviewLifetime();
  const knownRepairs = useRef(new Map<string, ReviewRepairSummary>());
  const withKnownRepairs = (next: SavedReviewSummary[]) => next.map((review) => {
    const repair = knownRepairs.current.get(review.id);
    return repair ? { ...review, repair } : review;
  });
  const flagRepair = (reviewId: string, reason: unknown) => {
    if (!(reason instanceof ApiError) || reason.status !== 409 || reason.code !== "review_needs_repair" || !reason.repair) return false;
    const repair = reason.repair;
    knownRepairs.current.set(reviewId, repair);
    setReviews((current) => current.map((review) => review.id === reviewId ? { ...review, repair } : review));
    setSelected((current) => current?.id === reviewId ? { ...current, repair } : null);
    if (selected?.id !== reviewId) setDraft(null);
    setError(null);
    return true;
  };
  const refreshReviews = async () => {
    const next = await fetchSavedReviews(item.id, { media_source_id: mediaSourceId, sort, direction });
    if (!isCurrent()) return;
    knownRepairs.current.clear();
    setReviews(next);
  };
  const refreshAfterConflict = async (reason: unknown) => {
    if (!isCurrent()) return;
    if (selected && reason instanceof ApiError && [403, 404].includes(reason.status) && ["not_found", "media_unavailable", "media_denied"].includes(reason.code ?? "")) {
      const id = selected?.id;
      reviewLoadRequest.current += 1;
      setSelected(null); setDraft(null); setConflict(null);
      setReviews(current => current.filter(review => review.id !== id));
      return;
    }
    if (!(reason instanceof ApiError) || reason.status !== 409 || !selected) return;
    if (flagRepair(selected.id, reason)) return;
    if (reviewConflictOutcome(reason.code) !== "stale") {
      setDraft(current => current ? { ...current, revision: selected.revision,
        policy_owner_id: selected.policy_owner_id ?? null, policy_revision: selected.policy_revision ?? 0 } : current);
      setConflict(null);
      return;
    }
    setConflict("loading");
    const request = ++reviewLoadRequest.current;
    try {
      const detail = await fetchSavedReview(selected.id);
      if (!isCurrent() || request !== reviewLoadRequest.current) return;
      setSelected(detail);
      setConflict("ready");
      setReviews((current) => current.map((review) => review.id === detail.id ? detail : review));
    } catch (error) {
      if (!isCurrent() || request !== reviewLoadRequest.current) return;
      if (error instanceof ApiError && [403, 404].includes(error.status)) {
        setSelected(null); setDraft(null); setConflict(null);
        setReviews(current => current.filter(review => review.id !== selected.id));
        return;
      }
      if (flagRepair(selected.id, error)) return;
      setConflict("failed");
    }
  };

  useEffect(() => {
    let active = true;
    void fetchSavedReviews(item.id, { media_source_id: mediaSourceId, sort, direction }).then((next) => {
      if (active) setReviews(withKnownRepairs(next));
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "Unable to load saved reviews.");
    });
    return () => { active = false; };
  }, [direction, item.id, mediaSourceId, sort]);

  useEffect(() => {
    let active = true;
    const request = ++reviewLoadRequest.current;
    knownRepairs.current.clear();
    setSelected(null);
    setDraft(null);
    setConflict(null);
    setError(null);
    resetMetadata();
    setBusy(false);
    void fetchSavedReviews(item.id, {
      media_source_id: mediaSourceId, sort: "updated_at", direction: "desc",
    }).then(async (next) => {
      if (!active || !isCurrent() || request !== reviewLoadRequest.current) return;
      setReviews(withKnownRepairs(next));
      const mostRecent = next[0];
      if (!mostRecent) {
        setDraft(createReviewDraft(null, "", catalogMcfDocument()));
        return;
      }
      if (mostRecent.repair) return;
      try {
        const detail = await fetchSavedReview(mostRecent.id);
        if (active && isCurrent() && request === reviewLoadRequest.current) {
          setSelected(detail);
          setDraft(createReviewDraft(detail));
        }
      } catch (reason) {
        if (!active || request !== reviewLoadRequest.current) return;
        if (!flagRepair(mostRecent.id, reason)) throw reason;
      }
    }).catch((reason) => {
      if (active && request === reviewLoadRequest.current) {
        setError(reason instanceof Error ? reason.message : "Unable to load saved reviews.");
      }
    });
    return () => { active = false; };
  }, [item.id, mediaSourceId]);

  useEffect(() => {
    if (!importedReview) return;
    reviewLoadRequest.current += 1;
    setBusy(false);
    setSelected(importedReview);
    setDraft(createReviewDraft(importedReview));
    setConflict(null);
    setEditingReviewName(false);
    resetMetadata();
    setReviews((current) => [importedReview, ...current.filter((review) => review.id !== importedReview.id)]);
  }, [importedReview]);

  const selectReview = async (reviewId: string) => {
    if (!isCurrent()) return;
    if (reviews.find((review) => review.id === reviewId)?.repair) return;
    if (draft && isReviewDraftDirty(draft) && !window.confirm("Discard unsaved review changes?")) return;
    const request = ++reviewLoadRequest.current;
    setBusy(true); setError(null);
    try {
      const detail = await fetchSavedReview(reviewId);
      if (!isCurrent() || request !== reviewLoadRequest.current) return;
      setSelected(detail);
      setDraft(createReviewDraft(detail));
      setConflict(null);
      setEditingReviewName(false);
    } catch (reason) {
      if (!isCurrent() || request !== reviewLoadRequest.current) return;
      if (!flagRepair(reviewId, reason)) setError(reason instanceof Error ? reason.message : "Unable to load saved review.");
    } finally {
      if (request === reviewLoadRequest.current) setBusy(false);
    }
  };

  return { reviews, setReviews, selected, setSelected, draft, setDraft, conflict, setConflict, reviewLoadRequest,
    selectReview, refreshAfterConflict, refreshReviews };
}