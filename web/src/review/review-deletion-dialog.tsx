import { useEffect, useRef, useState } from "react";
import type { ExportPublication } from "../api/api-mcf-types";
import type { ReviewDeletionPreview, ReviewDeletionResult, ReviewFileChoice, SavedReview } from "../api/api-review-types";
import { deleteReviewWithChoices, fetchReviewDeletion } from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { ModalDialog } from "../ui/ModalDialog";
import { ExportConditionText } from "./export-publication-view";
import { useAccountLabels, useReviewLifetime } from "./review-access";

const STALE = new Set(["stale_review_deletion", "stale_review_revision", "review_deletion_choice_required", "capability_denied"]);

/** Keep a still-listed file's earlier choice; default new files to the non-destructive retain. */
export function initialChoices(preview: ReviewDeletionPreview, previous: Record<number, ReviewFileChoice> = {}): Record<number, ReviewFileChoice> {
  return Object.fromEntries(preview.files.map(({ publication, choices }) => {
    const kept = previous[publication.id];
    return [publication.id, kept && choices.includes(kept) ? kept : "retain"];
  }));
}

/**
 * Delete a saved review with an explicit retain/remove choice for each
 * published file the caller may see. The request carries the preview's
 * confirmation, so any change since the preview refuses and reloads it.
 */
export function ReviewDeletionDialog({ review, onDeleted, onClose }: {
  review: Pick<SavedReview, "id" | "name" | "updated_at">;
  onDeleted: (result: ReviewDeletionResult, files: ExportPublication[]) => void;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<ReviewDeletionPreview | null>(null);
  const [choices, setChoices] = useState<Record<number, ReviewFileChoice>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isCurrent = useReviewLifetime();
  const label = useAccountLabels(preview?.files.map(({ publication }) => publication.target_user_id) ?? []);

  const latestRequest = useRef(0);

  /** Show the newest request's preview or failure only, merging a preview into the choices the user holds when it arrives. */
  const load = async () => {
    const request = ++latestRequest.current;
    const newest = () => isCurrent() && request === latestRequest.current;
    try {
      const next = await fetchReviewDeletion(review.id);
      if (!newest()) return;
      setPreview(next);
      setChoices((current) => initialChoices(next, current));
    } catch (reason) {
      if (newest()) setError(reason instanceof Error ? reason.message : "Unable to preview review deletion.");
    }
  };
  useEffect(() => { void load(); }, [review.id]);

  const confirm = async () => {
    if (!preview || busy) return;
    setBusy(true); setError(null);
    try {
      const result = await deleteReviewWithChoices(review.id, {
        revision: preview.revision, confirmation: preview.confirmation,
        files: preview.files.map(({ publication }) => ({ export_id: publication.id, choice: choices[publication.id] })),
      });
      if (isCurrent()) onDeleted(result, preview.files.map(({ publication }) => publication));
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof ApiError && STALE.has(reason.code ?? "")) {
        setError("The review or its published files changed since this list was shown. Check the current files and confirm again.");
        await load();
      } else setError(reason instanceof Error ? reason.message : "Unable to delete saved review.");
    } finally { if (isCurrent()) setBusy(false); }
  };

  const owner = (publication: ExportPublication) => publication.scope === "default" ? "Item default" : `Personal · ${label(publication.target_user_id)}`;
  return <ModalDialog labelledBy="review-deletion-heading" className="review-deletion-dialog" onClose={busy ? undefined : onClose}>
    <h2 id="review-deletion-heading">Delete review</h2>
    <p>Delete “{review.name}” (modified {new Date(review.updated_at).toLocaleString()})? Its scans and every other saved review are kept.</p>
    {!preview && !error && <p className="muted-copy" role="status">Checking published files…</p>}
    {preview && preview.files.length > 0 && <fieldset className="review-deletion-files" disabled={busy}>
      <legend>Published files from this review</legend>
      {preview.files.map(({ publication, choices: allowed }) => {
        const name = `${publication.format.toUpperCase()} · ${owner(publication)}`;
        return <div key={publication.id} className="review-deletion-file" role="group" aria-label={name}>
          <div><strong>{name}</strong><ExportConditionText publication={publication} source="this review" /></div>
          <div className="review-deletion-choices">
            <label><input type="radio" name={`file-${publication.id}`} checked={choices[publication.id] === "retain"}
              onChange={() => setChoices((current) => ({ ...current, [publication.id]: "retain" }))} /> Keep file</label>
            {allowed.includes("remove")
              ? <label><input type="radio" name={`file-${publication.id}`} checked={choices[publication.id] === "remove"}
                onChange={() => setChoices((current) => ({ ...current, [publication.id]: "remove" }))} /> Remove file</label>
              : <small>You can't remove this file, so it is kept.</small>}
          </div>
        </div>;
      })}
      <p className="muted-copy">Kept files stay published and remain listed under Published files as coming from a deleted review, with their owner and policy unchanged.</p>
    </fieldset>}
    {preview && preview.files.length === 0 && <p className="muted-copy">No published files you can see come from this review.</p>}
    {preview && preview.legacy_formats.length > 0 && <p className="muted-copy">Legacy {preview.legacy_formats.map((format) => format.toUpperCase()).join(" and ")} sidecars beside the media are always kept.</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="button-row">
      <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
      <button type="button" className="danger-button" onClick={() => void confirm()} disabled={busy || !preview}>{busy ? "Deleting…" : "Delete review"}</button>
    </div>
  </ModalDialog>;
}
