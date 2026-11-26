import { Fragment } from "react";
import type { SavedReviewSummary } from "../api/api-review-types";
import type { useSavedReviewWorkspace } from "./review-workspace-state";
import { ImportDialog } from "./review-import";
import { ModalDialog } from "../ui/ModalDialog";
import { ExportBadges } from "./export-status";
import { isReviewDraftDirty } from "./review-draft";

type Workspace = ReturnType<typeof useSavedReviewWorkspace>;

type ReviewNameDialogProps = {
  mode: "new" | "duplicate";
  name: string;
  error: string | null;
  normalized: string | null;
  busy: boolean;
  onNameChange: (name: string) => void;
  onClose: () => void;
  onSave: () => void;
};

/** Reuse review name validation and actions for creation and duplication.
 * @param props Dialog mode, name state, and action callbacks.
 */
function ReviewNameDialog({ mode, name, error, normalized, busy, onNameChange, onClose, onSave }: ReviewNameDialogProps) {
  const isNew = mode === "new";
  const title = isNew ? "New Review" : "Duplicate Review";
  const headingId = isNew ? "new-review-heading" : "duplicate-review-heading";
  return <ModalDialog labelledBy={headingId} onClose={onClose}>
    <h2 id={headingId}>{title}</h2>
    <label className="field">
      <span>Review name</span>
      <input
        autoFocus
        aria-label={`${isNew ? "New" : "Duplicate"} review name`}
        value={name}
        aria-invalid={Boolean(name && error)}
        onChange={(event) => onNameChange(event.target.value)}
      />
    </label>
    {name && error && <span className="form-error" role="alert">{error}</span>}
    <div className="button-row">
      <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
      <button type="button" className="btn-primary" onClick={onSave} disabled={busy || !normalized || Boolean(error)}>
        {isNew ? "Start new review" : "Duplicate"}
      </button>
    </div>
  </ModalDialog>;
}

/** Show saved reviews, import controls, and review creation dialogs.
 * @param workspace Review state and list actions.
 */
export function ReviewWorkspaceList({ workspace, onResolve, showImport = true }: {
  workspace: Workspace;
  onResolve: (review: SavedReviewSummary, opener: HTMLButtonElement) => void;
  showImport?: boolean;
}) {
  const {
    reviews, selected, name, sort, direction, busy, showNewReview, showDuplicateReview,
    rowExportStatus, pendingNameValidation,
    setName, setError, setShowNewReview, setShowDuplicateReview,
    toggleReviewSort, selectReview, createEmpty, duplicateReview, access, conflict,
  } = workspace;
  const closeNew = () => { setShowNewReview(false); setName(""); };
  const closeDuplicate = () => { setShowDuplicateReview(false); setName(""); };

  return <>
    <div className="button-row saved-review-table-actions">
      {access.create && <button
        type="button"
        className="btn-primary"
        onClick={() => { setName(""); setError(null); setShowNewReview(true); }}
        disabled={busy}
      >New Review</button>}
      {showImport && <ReviewImportAction workspace={workspace} />}
    </div>
    <div className="library-table-wrap saved-review-table-wrap" role="region" aria-label="Saved reviews table" tabIndex={0}>
      <table className="library-table saved-review-table" aria-label="Saved reviews">
        <thead><tr>
          <th aria-sort={sort === "name" ? direction === "asc" ? "ascending" : "descending" : "none"}>
            <button type="button" className="sort-button" onClick={() => toggleReviewSort("name")}>Name
              {sort === "name" && <span aria-hidden="true"> {direction === "asc" ? "▲" : "▼"}</span>}
            </button>
          </th>
          <th aria-sort={sort === "updated_at" ? direction === "asc" ? "ascending" : "descending" : "none"}>
            <button type="button" className="sort-button" onClick={() => toggleReviewSort("updated_at")}>Modified
              {sort === "updated_at" && <span aria-hidden="true"> {direction === "asc" ? "▲" : "▼"}</span>}
            </button>
          </th>
          <th>Exports</th>
        </tr></thead>
        <tbody>
          {reviews.map((review) => <Fragment key={review.id}><tr className={selected?.id === review.id ? "selected" : ""}>
            <td><button
              type="button"
              className="saved-review-table-select"
              aria-label={`Open review ${review.name}`}
              onClick={() => void selectReview(review.id)}
              disabled={busy || Boolean(review.repair)}
            ><strong>{review.name}</strong></button></td>
            <td><time dateTime={review.updated_at} title={new Date(review.updated_at).toLocaleString()}>
              {new Date(review.updated_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}</time></td>
            <td><ExportBadges status={rowExportStatus(review)} /></td>
          </tr>{review.repair && <tr className="saved-review-repair-row"><td colSpan={3}>
            <div className="saved-review-repair-message">
              <span className="review-repair-icon" role="img" aria-label="Review needs repair">!</span>
              <span>{review.repair.message}</span>
              <button type="button" className="btn-secondary compact-button" aria-label={`Resolve ${review.name}`}
                disabled={busy} onClick={(event) => onResolve(review, event.currentTarget)}>Resolve</button>
            </div>
          </td></tr>}</Fragment>)}
          {!reviews.length && <tr><td colSpan={3} className="muted-copy">No saved reviews yet.</td></tr>}
        </tbody>
      </table>
    </div>
    {showNewReview && <ReviewNameDialog
      mode="new"
      name={name}
      error={pendingNameValidation.error}
      normalized={pendingNameValidation.normalized}
      busy={busy || Boolean(conflict) || !access.create}
      onNameChange={setName}
      onClose={closeNew}
      onSave={createEmpty}
    />}
    {showDuplicateReview && <ReviewNameDialog
      mode="duplicate"
      name={name}
      error={pendingNameValidation.error}
      normalized={pendingNameValidation.normalized}
      busy={busy || Boolean(conflict) || !access.duplicate}
      onNameChange={setName}
      onClose={closeDuplicate}
      onSave={() => void duplicateReview()}
    />}
  </>;
}

/** Offer EDL and MCF import for the selected version using the review's permissions and draft guard. */
export function ReviewImportAction({ workspace, onComplete, stageForVerification }: {
  workspace: Workspace;
  onComplete?: () => void;
  stageForVerification?: boolean;
}) {
  const { item, mediaSourceId, onImported, reviewTarget, completedCreditsScans, busy, draft } = workspace;
  const version = item.versions?.find((value) => value.id === mediaSourceId);
  return <ImportDialog
    target={reviewTarget}
    itemName={item.name}
    versionName={version?.name}
    durationSeconds={version?.duration_seconds ?? item.duration_seconds ?? null}
    creditsScans={completedCreditsScans}
    onImported={(result) => { onImported(result); if (result.review) onComplete?.(); }}
    stageForVerification={stageForVerification}
    disabled={busy || Boolean(draft && isReviewDraftDirty(draft))}
  />;
}
