import { useState } from "react";
import type { useSavedReviewWorkspace } from "./review-workspace-state";
import { SavedReviewOverview } from "./review-overview";
import { ManualReviewFilterDialog } from "./review-controls";
import { ExportDialog } from "./review-export-dialog";
import { ReviewDeletionDialog } from "./review-deletion-dialog";
import { canDownload, publishDestinations } from "./export-model";
import { usePrincipal } from "../auth/auth-gate";
import { scanLabel } from "../shared/display";
import { ReviewScanInfo, reviewScanName } from "./review-scan-source";
import { ShareAction } from "../scans/sharing-dialog";
import { useReviewAccountLabel } from "./review-access";
import { ReviewCommunityEntries } from "./review-community-sources";
import { hasExportEvidence } from "./review-community";
import { useReviewCommunity } from "./use-review-community";
import { useReviewCommunityVotes } from "./use-review-community-votes";
import {
  isReviewDraftDirty, isReviewDraftContentDirty, updateReviewFilter,
  deleteReviewFilter, discardReviewDraft, stageManualReviewFilter,
} from "./review-draft";

type Workspace = ReturnType<typeof useSavedReviewWorkspace>;

/** Edit the selected review's name, filters, scans, and exports.
 * @param workspace Review state and edit actions.
 */
export function ReviewWorkspaceEditor({ workspace, guided = false }: { workspace: Workspace; guided?: boolean }) {
  const accountLabel = useReviewAccountLabel(workspace.selected);
  const me = usePrincipal()?.userId ?? null;
  const [dialog, setDialog] = useState<"export" | "delete" | null>(null);
  const community = useReviewCommunity(workspace);
  const votes = useReviewCommunityVotes(community.sources);
  const {
    scans, selected, draft, busy, preview, editingReviewName, reviewNameBeforeEdit,
    showManualFilter, preflightFocusFilterId, draftNameValidation,
    invalidDraftClassification, metadataSourceRequired, invalidMcfDocument,
    availableScans, setName, setError, setDraft, setMetadataCandidates,
    setApplyStatus, setShowDuplicateReview, setEditingReviewName,
    setReviewNameBeforeEdit, setShowManualFilter, setPreflightFocusFilterId,
    renameReview, applyFilterDefaults, previewFilter, clearPreview, save, reviews, exports,
    toggleScanMembership, changeScanMembership, access, conflict,
  } = workspace;
  if (!draft) return null;
  const exportable = !guided && selected !== null && (publishDestinations(selected.owner_id, me, access.allowed).length > 0 || canDownload(selected.owner_id, me, access.allowed));

  const saveDisabled = busy || Boolean(conflict) || !access.save || invalidMcfDocument || invalidDraftClassification
    || metadataSourceRequired || Boolean(draftNameValidation?.error)
    || !draftNameValidation?.normalized || Boolean(selected && !isReviewDraftDirty(draft));
  const renameDisabled = busy || Boolean(conflict) || !access.rename || !draftNameValidation?.normalized
    || Boolean(draftNameValidation.error)
    || Boolean(selected && (
      isReviewDraftContentDirty(draft)
      || draftNameValidation.normalized === selected.name
    ));
  const combinedScans = availableScans.filter((scan) => scan.scan_audio && scan.scan_video);
  const hasCombinedMembership = combinedScans.some((scan) => draft.memberships.some((membership) => membership.scan_id === scan.id));

  const overview = <SavedReviewOverview
      requiredFilterIds={selected?.policy_required_filter_ids}
      lockedSeverities={selected?.policy_locked_severities}
      filters={draft.filters}
      memberships={draft.memberships}
      scans={[...(selected?.scans ?? []).map((reference) => reference.scan), ...scans]}
      defaultSort={guided ? "time" : "category"}
      busy={busy || Boolean(conflict)}
      filtersEditable={access.field("filters")}
      cuesEditable={access.field("cues")}
      metadataEditable={access.field("metadata") && !workspace.conflict && !selected?.repair}
      onToggleFilters={(filterIds, enabled) => setDraft((current) => current
        ? filterIds.reduce((draft, filterId) => updateReviewFilter(draft, filterId, { selected: enabled }), current)
        : current)}
      onChangeFilter={(filterId, patch) => setDraft((current) => current ? updateReviewFilter(current, filterId, patch) : current)}
      onDeleteFilter={(filterId) => setDraft((current) => current ? deleteReviewFilter(current, filterId) : current)}
      onApplyDefaults={() => void applyFilterDefaults()}
      onPreviewFilter={(filter) => void previewFilter(filter)}
      preview={preview}
      previewDisabled={!access.preview}
      focusFilterId={preflightFocusFilterId}
      onFocusFilterComplete={() => setPreflightFocusFilterId(null)}
      renderFilterVote={votes.renderVote}
    />;
  const results = overview;
  const memberships = <div className="saved-review-memberships">
        <strong>Included scans</strong>
        {availableScans.map((scan) => {
          const included = draft.memberships.some((membership) => membership.scan_id === scan.id);
          return <span className="review-scan-source" key={scan.id}>
            <label><input
              type="checkbox"
              checked={included}
              disabled={busy || !access.field("evidence")}
              onChange={(event) => void toggleScanMembership(scan, event.target.checked)}
            /> {reviewScanName(scan)}</label>
            <ReviewScanInfo scans={[scan]} />
          </span>;
        })}
        <ReviewCommunityEntries entries={community.entries} draft={draft} maySearch={community.maySearch}
          disabled={busy || !access.field("evidence")} onChange={setDraft}
          votesFailed={votes.loadFailed} onRetryVotes={votes.retry} />
      </div>;
  const roles = hasCombinedMembership && <div className="saved-review-membership-roles">
      <strong>Combined scan roles</strong>
      {combinedScans.map((scan) => {
        const membership = draft.memberships.find((item) => item.scan_id === scan.id);
        return membership && <label className="field" key={scan.id}>
          <span>{reviewScanName(scan)}</span>
          <select
            aria-label={`Review role for ${scanLabel(scan)}`}
            value={membership.role}
            disabled={busy || !access.field("evidence")}
            onChange={(event) => void changeScanMembership(scan, event.target.value as typeof membership.role)}
          >
            <option value="audio">Audio only</option>
            <option value="video">Video only</option>
            <option value="both">Audio and video</option>
          </select>
        </label>;
      })}
    </div>;
  const evidence = <aside className="guided-review-evidence" aria-label="Review scan evidence">{memberships}{roles}</aside>;
  const saveControls = <div className="button-row review-save-actions">
        <button type="button" className="btn-primary" onClick={() => void save()} disabled={saveDisabled}>
          {busy ? "Saving…" : selected ? "Save Changes" : "Save Review"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            setDraft(discardReviewDraft(draft));
            setMetadataCandidates([]);
            setApplyStatus(null);
            setEditingReviewName(false);
          }}
          disabled={busy || !isReviewDraftDirty(draft)}
        >Discard</button>
        {exportable && <button type="button" className="btn-secondary" aria-haspopup="dialog"
          disabled={busy || Boolean(conflict) || isReviewDraftDirty(draft)}
          title={busy ? "Wait for the current review action to finish" : isReviewDraftDirty(draft) ? "Save or discard review changes before exporting" : undefined}
          onClick={() => { setError(null); setDialog("export"); }}>Export</button>}
      </div>;

  return <>
    <div className="saved-review-current-header">
      <div className="saved-review-current-title">
        {editingReviewName ? <div className="saved-review-name-editor">
          <label>
            <span className="sr-only">Review name</span>
            <input
              autoFocus
              aria-label="Review name"
              value={draft.name}
              aria-invalid={Boolean(draft.name && draftNameValidation?.error)}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            />
          </label>
          <button
            type="button"
            className="btn-primary compact-button"
            onClick={() => { if (selected) void renameReview(); else setEditingReviewName(false); }}
            disabled={renameDisabled}
          >Save</button>
          <button
            type="button"
            className="btn-secondary compact-button"
            onClick={() => { setDraft({ ...draft, name: reviewNameBeforeEdit }); setEditingReviewName(false); }}
            disabled={busy}
          >Cancel</button>
        </div> : <>
          <h3>{draft.name || "Untitled review"}</h3>
          {access.rename && <button
            type="button"
            className="icon-button saved-review-rename-button"
            aria-label="Rename review"
            title="Rename review"
            onClick={() => { setReviewNameBeforeEdit(draft.name); setEditingReviewName(true); }}
            disabled={busy || Boolean(selected && isReviewDraftContentDirty(draft))}
          >✎</button>}
        </>}
      </div>
      {selected && <div className="button-row saved-review-current-actions">
        {!guided && <ShareAction source={{ kind: "review", id: selected.id }} name={selected.name}
          disabled={busy || isReviewDraftDirty(draft)} />}
        {access.duplicate && <button
          type="button"
          className="btn-secondary compact-button"
          onClick={() => { setName(""); setError(null); setShowDuplicateReview(true); }}
          disabled={busy || Boolean(conflict)}
        >Duplicate</button>}
        {access.remove && <button
          type="button"
          className="danger-button compact-button"
          aria-label="Delete review"
          onClick={() => setDialog("delete")}
          disabled={busy || Boolean(conflict)}
        >Delete Review</button>}
      </div>}
      {guided && saveControls}
    </div>
    {selected && <p className="muted-copy">Current owner: {accountLabel(selected.owner_id)}. Historical author: {accountLabel(selected.original_author_id)}. Policy source: {selected.policy_owner_id === selected.owner_id ? "current review owner" : accountLabel(selected.policy_owner_id)}, revision {selected.policy_revision}.</p>}
    {guided ? <div className="guided-review-editor">{evidence}{results}</div> : results}
    <div className="saved-review-editor">
      {editingReviewName && draft.name && draftNameValidation?.error && <span className="form-error" role="alert">
        {draftNameValidation.error}
      </span>}
      {!guided && memberships}
      {!guided && saveControls}

    </div>
    {!guided && roles}
    <div className="button-row">
      <button
        type="button"
        className="btn-secondary"
        onClick={() => { setError(null); setShowManualFilter(true); }}
        disabled={busy || !access.field("cues")}
      >Add Manual Filter</button>
      {draft.newManualFilters.length > 0 && <span className="form-status" role="status">
        {draft.newManualFilters.length} Manual {draft.newManualFilters.length === 1 ? "filter" : "filters"} staged
      </span>}
    </div>
    {selected && dialog === "export" && <ExportDialog review={selected} reviews={reviews} exports={exports} allowed={access.allowed}
      exportAvailable={hasExportEvidence(selected)} onMcfIssues={workspace.setMcfPreflightIssues}
      onConflict={workspace.refreshAfterConflict} onPublished={() => void workspace.afterPublish()} onClose={() => setDialog(null)} />}
    {selected && dialog === "delete" && <ReviewDeletionDialog review={selected} onClose={() => setDialog(null)}
      onDeleted={(result) => { setDialog(null); workspace.forgetDeletedReview(result.review_id, result.retained.length, result.removed.length); }} />}
    {showManualFilter && <ManualReviewFilterDialog
      disabled={busy || Boolean(conflict) || !access.field("cues")}
      preview={preview}
      previewError={workspace.error}
      onPreview={access.preview ? (filter) => void previewFilter(filter) : undefined}
      onClose={() => { clearPreview(); setError(null); setShowManualFilter(false); }}
      onAdd={(filter) => { setDraft(stageManualReviewFilter(draft, filter)); setShowManualFilter(false); }}
    />}
  </>;
}
