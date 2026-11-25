import { useEffect, useState } from "react";
import { applyFilterDefaults as applyFilterDefaultsRequest } from "../api/api-service";
import { fetchScanFindings } from "../api/api-scans";
import {
  fetchSavedReview,
  createSavedReview,
  saveSavedReview,
  renameSavedReview,
  duplicateSavedReview,
  reviewPolicyToken,
  applyReviewFilterDefaults,
} from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import type { JellyfinMediaItem } from "../api/api-media-types";
import type { ExportFormat, ExportStatus, McfRegistry } from "../api/api-mcf-types";
import type { Scan } from "../api/api-scan-types";
import type {
  SavedReview,
  SavedReviewSummary,
  ReviewTargetRequest,
  McfMetadataSeedCandidate,
  ReviewMcfDocument,
  McfPreflightIssue,
} from "../api/api-review-types";
import type { CreditsAcceptanceResult } from "../api/api-credits-types";
import {
  changeReviewMembership, applyCreditsAcceptance, createReviewDraft,
  isReviewDraftDirty, isReviewDraftContentDirty, replaceReviewDraftAfterSave,
  membershipHasRole, removedSnapshotKey, snapshotReviewFilters,
  reviewDraftFilters, applyReviewFilterResult,
} from "./review-draft";
import { validateDisplayName } from "../shared/name-validation";
import { loadMcfRegistry } from "../shared/mcf-registry";
import type { ImportResult } from "./review-types";
import { exportStatusForReview } from "./export-status";
import { useVersionPublications } from "./export-publications";
import { classificationIsValid, validateMcfDocument } from "./review-mcf";
import { useReviewPreview } from "./review-preview";
import { catalogReviewMcfDocument, focusMcfPreflightIssue } from "./review-workspace-metadata-state";
import { useReviewSelection } from "./review-selection";
import { useReviewAccess, useReviewLifetime } from "./review-access";

export type SavedReviewPanelProps = {
  item: Pick<JellyfinMediaItem, "id"> & Partial<Pick<JellyfinMediaItem, "item_type" | "name" | "year" | "season_number" | "index_number" | "duration_seconds" | "versions">>;
  /** The selected media's persisted item ID, when the caller knows it without a saved review. */
  mediaItemId?: string | null;
  seasonNumber?: number | null;
  mediaSourceId: string | null;
  stableVersionId?: string;
  scans: Scan[];
  importedReview?: SavedReview | null;
  exportStatus?: ExportStatus;
  onExportStatusChange?: (status: ExportStatus) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onSaved?: () => void;
  onImported: (result: ImportResult) => void;
};

/** Coordinate saved review selection, draft edits, imports, and exports.
 * @param props Media version, scans, and callbacks for the review workspace.
 */
export function useSavedReviewWorkspace({
  item,
  mediaItemId,
  seasonNumber,
  mediaSourceId,
  stableVersionId,
  scans,
  importedReview,
  exportStatus,
  onExportStatusChange,
  onDirtyChange,
  onSaved,
  onImported,
}: SavedReviewPanelProps) {

  const [name, setName] = useState("");
  const [sort, setSort] = useState<"name" | "updated_at">("updated_at");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNewReview, setShowNewReview] = useState(false);
  const [showDuplicateReview, setShowDuplicateReview] = useState(false);
  const [editingReviewName, setEditingReviewName] = useState(false);
  const [reviewNameBeforeEdit, setReviewNameBeforeEdit] = useState("");
  const [showManualFilter, setShowManualFilter] = useState(false);
  const [mcfRegistry, setMcfRegistry] = useState<McfRegistry | null>(null);
  const [metadataCandidates, setMetadataCandidates] = useState<McfMetadataSeedCandidate[]>([]);
  const [applyStatus, setApplyStatus] = useState<string | null>(null);
  const [mcfPreflightIssues, setMcfPreflightIssues] = useState<McfPreflightIssue[]>([]);
  const [preflightFocusFilterId, setPreflightFocusFilterId] = useState<string | null>(null);
  const catalogMcfDocument = (): ReviewMcfDocument => catalogReviewMcfDocument(item, seasonNumber, mediaSourceId);
  const {
    reviews, setReviews, selected, setSelected, draft, setDraft, reviewLoadRequest,
    selectReview, refreshAfterConflict, refreshReviews, conflict, setConflict,
  } = useReviewSelection({
    item, mediaSourceId, stableVersionId, importedReview, sort, direction, catalogMcfDocument,
    setBusy, setError, setEditingReviewName,
    resetMetadata: () => { setMetadataCandidates([]); setApplyStatus(null); },
  });
  const isCurrent = useReviewLifetime();
  const access = useReviewAccess(selected);
  const exports = useVersionPublications({
    itemId: item.id, mediaItemId: mediaItemId ?? selected?.media_item_id ?? reviews[0]?.media_item_id ?? null,
    mediaSourceId, exportStatus, onExportStatusChange,
  });
  const reviewTarget: ReviewTargetRequest = { source: "jellyfin", item_id: item.id, media_source_id: mediaSourceId };
  const { preview, previewFilter, clearPreview } = useReviewPreview({ target: reviewTarget, selected, draft, setBusy, setError,
    disabled: Boolean(conflict) || !access.preview, onDenied: refreshAfterConflict });
  useEffect(() => {
    setMcfPreflightIssues([]);
    setPreflightFocusFilterId(null);
  }, [selected?.id]);
  const pendingNameValidation = validateDisplayName(name, true);
  const draftNameValidation = draft ? validateDisplayName(draft.name, true) : null;
  const invalidDraftClassification = Boolean(draft && mcfRegistry && draft.filters.some((filter) => (
    (Boolean(filter.category) !== Boolean(filter.subcategory))
    || (Boolean(filter.category) && !classificationIsValid(mcfRegistry, filter.category, filter.subcategory))
    || Boolean(filter.mcf_comment?.includes("-->"))
  )));
  const metadataSourceRequired = metadataCandidates.length > 0 && !draft?.mcfMetadataSeed;
  const invalidMcfDocument = Boolean(draft && Object.keys(validateMcfDocument(draft.mcfDocument)).length);
  const toggleReviewSort = (nextSort: typeof sort) => {
    if (sort === nextSort) setDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSort(nextSort); setDirection("asc"); }
  };

  useEffect(() => {
    void loadMcfRegistry().then(setMcfRegistry).catch((reason) => {
      setError(reason instanceof Error ? reason.message : "Unable to load MCF categories.");
    });
  }, []);

  useEffect(() => {
    if (!exportStatus) return;
    const exportedReviews = Object.values(exportStatus.exports).map((status) => status.exported_review).filter(Boolean);
    setReviews((current) => current.map((review) => {
      const exportedReview = exportedReviews.find((candidate) => candidate?.id === review.id);
      const exports = exportStatusForReview(exportStatus, review.id);
      return { ...review, ...exportedReview, exports };
    }));
    setSelected((current) => {
      if (!current) return current;
      const exportedReview = exportedReviews.find((candidate) => candidate?.id === current.id);
      const exports = exportStatusForReview(exportStatus, current.id);
      return { ...current, ...exportedReview, exports };
    });
  }, [exportStatus]);

  useEffect(() => {
    onDirtyChange?.(Boolean(draft && isReviewDraftDirty(draft)));
  }, [draft, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const applyFilterDefaults = async () => {
    if (!draft || conflict || !isCurrent() || !access.field("filters")) return;
    setBusy(true); setError(null);
    try {
      const result = selected
        ? await applyReviewFilterDefaults(selected.id, reviewDraftFilters(draft), draft)
        : await applyFilterDefaultsRequest(reviewDraftFilters(draft));
      if (!isCurrent()) return;
      setDraft((current) => current ? applyReviewFilterResult(current, result) : current);
      setApplyStatus(`${result.selection_changes} selection, ${result.severity_changes} severity, and ${result.channel_changes} channel changes applied to this draft.`);
    } catch (reason) {
      await refreshAfterConflict(reason);
      setError(reason instanceof Error ? reason.message : "Unable to apply filter defaults.");
    } finally { setBusy(false); }
  };

  const stageCreditsAcceptance = (result: CreditsAcceptanceResult) => {
    setDraft((current) => {
      if (!current) return current;
      const boundary = result.accepted_credits.accepted_boundary_seconds;
      const accepted = applyCreditsAcceptance(current, result);
      return {
        ...accepted,
        synchronization: accepted.synchronization.end_mode === "before_credits"
          ? { ...accepted.synchronization, end_seconds: boundary }
          : accepted.synchronization,
        mcfDocument: { ...accepted.mcfDocument, material_end_seconds: boundary },
      };
    });
  };

  const createEmpty = () => {
    if (!isCurrent() || !access.create) return;
    if (pendingNameValidation.error || !pendingNameValidation.normalized) {
      setError(pendingNameValidation.error || "Enter a review name before starting it.");
      return;
    }
    if (draft && isReviewDraftDirty(draft) && !window.confirm("Discard unsaved review changes?")) return;
    reviewLoadRequest.current += 1;
    setSelected(null);
    setDraft(createReviewDraft(null, pendingNameValidation.normalized, catalogMcfDocument()));
    setEditingReviewName(false);
    setName("");
    setShowNewReview(false);
    setError(null);
    setMetadataCandidates([]);
  };

  const save = async (resolveConflict = false) => {
    if (!draft || (conflict && !(resolveConflict && conflict === "ready")) || !isCurrent() || !access.save || (selected && !conflict && !isReviewDraftDirty(draft))) return;
    const token = resolveConflict && selected ? selected : draft;
    if (draftNameValidation?.error || !draftNameValidation?.normalized) {
      setError(draftNameValidation?.error || "Enter a review name before saving it.");
      return;
    }
    setBusy(true); setError(null);
    try {
      const saved = selected
        ? await saveSavedReview(selected.id, {
          ...reviewPolicyToken(token),
          revision: token.revision,
          name: draftNameValidation.normalized,
          memberships: draft.memberships,
          filters: reviewDraftFilters(draft),
          filter_basis: "base",
          mcf_document: draft.mcfDocument,
          synchronization: draft.synchronization,
          accepted_credits: draft.acceptedCredits,
          new_manual_filters: draft.newManualFilters,
          community_memberships: draft.communityMemberships,
        })
        : await createSavedReview({ ...reviewTarget, stable_version_id: stableVersionId }, {
          name: draftNameValidation.normalized,
          memberships: draft.memberships,
          filters: reviewDraftFilters(draft),
          new_manual_filters: draft.newManualFilters,
          mcf_metadata_seed: draft.mcfMetadataSeed,
          community_memberships: draft.communityMemberships.filter((value) => value.link_id),
        });
      if (!isCurrent()) return;
      setConflict(null);
      setSelected(saved);
      setDraft(replaceReviewDraftAfterSave(draft, saved));
      if (exportStatus && Object.values(exportStatus.exports).some((status) => status.review_id === saved.id)) {
        const afterSave = (format: ExportFormat) => {
          const summary = exportStatus.exports[format];
          if (summary.review_id !== saved.id) return summary;
          const outdated = summary.outdated || summary.exported_format_revision !== saved[`${format}_content_revision`];
          return { ...summary, current: summary.current && !outdated, outdated, exported_review: saved };
        };
        onExportStatusChange?.({ exports: { edl: afterSave("edl"), mcf: afterSave("mcf") } });
      }
      setMetadataCandidates([]);
      setApplyStatus(null);
      setReviews((current) => [saved, ...current.filter((review) => review.id !== saved.id)]);
      if (exports.publications.some((publication) => publication.review_id === saved.id)) void exports.refresh().catch(() => {});
      onSaved?.();
    } catch (reason) {
      await refreshAfterConflict(reason);
      if (reason instanceof ApiError && reason.code === "mcf_metadata_source_required") {
        setMetadataCandidates(reason.metadataCandidates);
        setError("Choose an MCF metadata source before saving this review.");
      } else {
        setError(reason instanceof Error ? reason.message : "Unable to save saved review.");
      }
    } finally { setBusy(false); }
  };

  const focusPreflightIssue = (issue: McfPreflightIssue) => {
    if (issue.filter_id) {
      setPreflightFocusFilterId(issue.filter_id);
      return;
    }
    focusMcfPreflightIssue(issue);
  };

  /** Reread the selected review after a publication unless another review load has superseded it. */
  const afterPublish = async () => {
    if (!selected || !isCurrent()) return;
    setMcfPreflightIssues([]);
    const request = ++reviewLoadRequest.current;
    try {
      const exported = await fetchSavedReview(selected.id);
      if (!isCurrent() || request !== reviewLoadRequest.current) return;
      setSelected(exported);
      setDraft(createReviewDraft(exported));
      setReviews((current) => current.map((review) => review.id === exported.id ? exported : review));
    } catch (reason) {
      if (request !== reviewLoadRequest.current) return;
      await refreshAfterConflict(reason);
      setError(reason instanceof Error ? reason.message : "Unable to read the exported review.");
    }
  };

  const renameReview = async () => {
    if (!selected || !draft || conflict || !isCurrent() || !access.rename || !draftNameValidation?.normalized || draftNameValidation.error || isReviewDraftContentDirty(draft)) return;
    setBusy(true); setError(null);
    try {
      const renamed = await renameSavedReview(selected.id, { revision: selected.revision, name: draftNameValidation.normalized });
      if (!isCurrent()) return;
      setSelected(renamed);
      setDraft(replaceReviewDraftAfterSave(draft, renamed));
      setReviews((current) => current.map((review) => review.id === renamed.id ? renamed : review));
      setEditingReviewName(false);
    } catch (reason) {
      await refreshAfterConflict(reason);
      setError(reason instanceof Error ? reason.message : "Unable to rename saved review.");
    } finally { setBusy(false); }
  };

  const duplicateReview = async () => {
    if (!selected || conflict || !isCurrent() || !access.duplicate || !pendingNameValidation.normalized || pendingNameValidation.error) return;
    if (draft && isReviewDraftDirty(draft) && !window.confirm("Discard unsaved changes and duplicate the last saved version of this review?")) return;
    setBusy(true); setError(null);
    try {
      const duplicate = await duplicateSavedReview(selected.id, { revision: selected.revision, name: pendingNameValidation.normalized, ...reviewPolicyToken(selected) });
      if (!isCurrent()) return;
      setReviews((current) => [duplicate, ...current]);
      setSelected(duplicate);
      setDraft(createReviewDraft(duplicate));
      setEditingReviewName(false);
      setName("");
      setShowDuplicateReview(false);
    } catch (reason) {
      await refreshAfterConflict(reason);
      setError(reason instanceof Error ? reason.message : "Unable to duplicate saved review.");
    } finally { setBusy(false); }
  };

  /** Drop a review the deletion dialog removed and report what happened to its files. */
  const forgetDeletedReview = (reviewId: string, retained: number, removed: number) => {
    if (!isCurrent()) return;
    setReviews((current) => current.filter((review) => review.id !== reviewId));
    if (selected?.id === reviewId) { setSelected(null); setDraft(null); setEditingReviewName(false); }
    exports.setNotice(retained + removed
      ? `Review deleted. Published files kept: ${retained}. Removed: ${removed}.`
      : "Review deleted.");
    void exports.refresh().catch(() => {});
  };

  const changeScanMembership = async (
    scan: Scan,
    role: SavedReview["memberships"][number]["role"] | null,
  ) => {
    if (!draft || conflict || !isCurrent() || !access.field("evidence")) return;
    const reviewId = draft.id;
    const previous = draft.memberships.find((membership) => membership.scan_id === scan.id);
    const membership = role ? { scan_id: scan.id, role } : null;
    const rolesToFetch = (["audio", "video"] as const).filter((candidate) => (
      membershipHasRole(membership ?? undefined, candidate)
      && !membershipHasRole(previous, candidate)
      && !draft.removedSnapshots[removedSnapshotKey(scan.id, candidate)]
    ));
    setDraft(changeReviewMembership(draft, membership, scan.id));
    if (!rolesToFetch.length) return;
    setBusy(true); setError(null);
    try {
      const result = await fetchScanFindings(scan.id);
      if (!isCurrent()) return;
      const snapshots = result.findings
        .filter((finding) => rolesToFetch.includes(finding.source === "audio" ? "audio" : "video"))
        .map(({ scan_id, ...finding }) => ({
          ...finding,
          id: String(finding.id),
          source_scan_id: scan_id,
          source_finding_id: finding.id,
        }));
      setDraft((current) => (
        current?.id === reviewId ? snapshotReviewFilters(current, snapshots.filter((filter) => {
          const currentMembership = current.memberships.find((item) => item.scan_id === scan.id);
          return membershipHasRole(currentMembership, filter.source === "audio" ? "audio" : "video");
        })) : current
      ));
    } catch (reason) {
      setDraft((current) => (
        current?.id === reviewId
          ? changeReviewMembership(current, previous ? { ...previous } : null, scan.id)
          : current
      ));
      setError(reason instanceof Error ? reason.message : "Unable to snapshot scan findings.");
    } finally { setBusy(false); }
  };

  const toggleScanMembership = (scan: Scan, included: boolean) => {
    const defaultRole = scan.scan_audio && scan.scan_video
      ? "both" as const
      : scan.scan_audio ? "audio" as const : "video" as const;
    void changeScanMembership(scan, included ? defaultRole : null);
  };

  const availableScans = scans.filter((scan) => scan.status === "complete");
  const completedCreditsScans = availableScans.filter((scan) => (
    scan.scan_mode === "credits_detection"
    && scan.credits_detection?.result?.outcome === "candidate"
    && Boolean(scan.credits_detection.result.best_candidate_id)
  ));
  const rowExportStatus = (review: SavedReviewSummary): ExportStatus => {
    if (!exportStatus) return review.exports;
    return exportStatusForReview(exportStatus, review.id);
  };
  return {
    item, mediaSourceId, scans, onImported, reviewTarget, reviews, selected, draft, conflict, setConflict, access,
    name, sort, direction, busy, error, preview, showNewReview, showDuplicateReview,
    editingReviewName, reviewNameBeforeEdit, showManualFilter, metadataCandidates,
    applyStatus, mcfPreflightIssues, preflightFocusFilterId, pendingNameValidation,
    draftNameValidation, invalidDraftClassification, metadataSourceRequired,
    invalidMcfDocument, availableScans, completedCreditsScans, rowExportStatus,
    setName, setError, setDraft, setMetadataCandidates, setApplyStatus,
    setShowNewReview, setShowDuplicateReview, setEditingReviewName,
    setReviewNameBeforeEdit, setShowManualFilter, setPreflightFocusFilterId,
    toggleReviewSort, selectReview, createEmpty, duplicateReview, renameReview,
    forgetDeletedReview, applyFilterDefaults, previewFilter, clearPreview, save, afterPublish, exports,
    setMcfPreflightIssues,
    toggleScanMembership, changeScanMembership, stageCreditsAcceptance,
    focusPreflightIssue, refreshAfterConflict, refreshReviews, setSelected,
  };
}
