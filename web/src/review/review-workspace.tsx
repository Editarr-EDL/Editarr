import { useRef, useState } from "react";
import type { SavedReviewSummary } from "../api/api-review-types";
import type { SavedReviewPanelProps } from "./review-workspace-state";
import { useSavedReviewWorkspace } from "./review-workspace-state";
import { ReviewWorkspaceMetadata } from "./review-workspace-metadata";
import { ReviewWorkspaceList } from "./review-workspace-list";
import { ReviewWorkspaceEditor } from "./review-workspace-editor";
import { ReviewRepairDialog } from "./review-repair-dialog";
import { ReviewDeletionDialog } from "./review-deletion-dialog";
import { ReviewConflict } from "./review-conflict";
import { PublishedFiles } from "./review-published-files";

/** Render the saved review workspace for one media version. Each version
 * gets a fresh workspace, so reviews, published files, and late responses
 * from previously selected media never appear under another one.
 * @param props Media version, scans, and parent update callbacks.
 */
export function SavedReviewPanel(props: SavedReviewPanelProps) {
  return <VersionWorkspace key={JSON.stringify([props.item.id, props.mediaSourceId ?? ""])} {...props} />;
}

function VersionWorkspace(props: SavedReviewPanelProps) {
  const workspace = useSavedReviewWorkspace(props);
  const { error, applyStatus, mcfPreflightIssues, focusPreflightIssue, selected } = workspace;
  const [resolvingReview, setResolvingReview] = useState<SavedReviewSummary | null>(null);
  const [deletingReview, setDeletingReview] = useState<SavedReviewSummary | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const openRepair = (review: SavedReviewSummary, opener: HTMLButtonElement) => {
    returnFocusRef.current = opener;
    setResolvingReview(review);
  };
  const resolved = () => {
    const reviewId = resolvingReview?.id;
    setResolvingReview(null);
    if (selected?.id === reviewId) { workspace.setSelected(null); workspace.setDraft(null); }
    void workspace.refreshReviews().catch((reason) => workspace.setError(reason instanceof Error ? reason.message : "Unable to load saved reviews."));
    props.onSaved?.();
  };

  return <section className="settings-card saved-review-panel" aria-labelledby="saved-reviews-heading">
    <div className="section-heading">
      <div>
        <h2 id="saved-reviews-heading">Saved reviews</h2>
        <p>Named review snapshots for this version.</p>
      </div>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <ReviewConflict workspace={workspace} />
    {applyStatus && <p className="form-status" role="status">{applyStatus}</p>}
    {mcfPreflightIssues.length > 0 && <section className="mcf-preflight-issues" aria-labelledby="mcf-preflight-heading" role="alert">
      <h3 id="mcf-preflight-heading">MCF export needs attention</h3>
      <p>Fix these saved review fields, save, then export again.</p>
      <ul>{mcfPreflightIssues.map((issue, index) => <li key={`${issue.code}:${issue.filter_id || issue.field}:${index}`}>
        <button type="button" onClick={() => focusPreflightIssue(issue)}>{issue.message}</button>
      </li>)}</ul>
    </section>}
    {!selected?.repair && <fieldset className="review-fieldset" disabled={Boolean(workspace.conflict)}><ReviewWorkspaceMetadata workspace={workspace} /></fieldset>}
    <ReviewWorkspaceList workspace={workspace} onResolve={openRepair} />
    <PublishedFiles exports={workspace.exports} reviews={workspace.reviews} selectedId={selected?.id ?? null} allowed={workspace.access.allowed} />
    {selected?.repair ? <>
      <div className="review-repair-alert" role="alert">
        <span>{selected.repair.message}</span>
        <button type="button" className="btn-secondary" onClick={(event) => openRepair(selected, event.currentTarget)}>Resolve</button>
      </div>
      <fieldset className="review-repair-readonly" disabled aria-label="Review awaiting repair">
        <ReviewWorkspaceMetadata workspace={workspace} />
        <ReviewWorkspaceEditor workspace={workspace} />
      </fieldset>
    </> : <fieldset className="review-fieldset" disabled={Boolean(workspace.conflict)}><ReviewWorkspaceEditor workspace={workspace} /></fieldset>}
    {resolvingReview && <ReviewRepairDialog key={resolvingReview.id} review={resolvingReview}
      onClose={() => setResolvingReview(null)} onResolved={resolved} returnFocusRef={returnFocusRef}
      onDelete={() => { setDeletingReview(resolvingReview); setResolvingReview(null); }} />}
    {deletingReview && <ReviewDeletionDialog review={deletingReview} onClose={() => setDeletingReview(null)}
      onDeleted={(result) => { setDeletingReview(null); workspace.forgetDeletedReview(result.review_id, result.retained.length, result.removed.length); props.onSaved?.(); }} />}
  </section>;
}
