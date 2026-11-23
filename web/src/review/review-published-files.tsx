import { useState, type ReactNode } from "react";
import { deletePublication } from "../api/api-exports";
import type { ExportPublication, ExportStatus } from "../api/api-mcf-types";
import type { SavedReviewSummary } from "../api/api-review-types";
import { ApiError } from "../api/api-transport";
import { usePrincipal } from "../auth/auth-gate";
import { canDeletePublication, type Allowed } from "./export-model";
import { ExportConditionText, publicationSource } from "./export-publication-view";
import type { VersionPublications } from "./export-publications";
import { useAccountLabels, useReviewLifetime } from "./review-access";
import { ExportBadges } from "./export-status";

/**
 * List every published file of this media version the caller may read, with
 * a delete action only where the caller holds the matching export capability.
 * Files retained after a review deletion stay listed here.
 */
export function PublishedFiles({ exports, reviews, selectedId, allowed, additionalFiles, legacy }: {
  exports: VersionPublications;
  reviews: SavedReviewSummary[];
  selectedId: string | null;
  allowed: Allowed;
  additionalFiles?: ReactNode;
  legacy?: ExportStatus["exports"];
}) {
  const me = usePrincipal()?.userId ?? null;
  const isCurrent = useReviewLifetime();
  const [confirming, setConfirming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = useAccountLabels(exports.publications.flatMap((row) => [row.actor_id, row.target_user_id, row.policy_owner_id]));
  const { publications, notice } = exports;
  const hasLegacy = legacy && Object.values(legacy).some((format) => format.present);
  if (!publications.length && !notice && !additionalFiles) return null;

  const owner = (publication: ExportPublication) => publication.scope === "default"
    ? "Item default"
    : `Personal · ${label(publication.target_user_id)}`;
  const remove = async (publication: ExportPublication) => {
    setDeleting(publication.id); setError(null);
    try {
      await deletePublication(publication);
      if (!isCurrent()) return;
      exports.setNotice(`Deleted ${publication.format.toUpperCase()} (${owner(publication)}). Saved reviews are unchanged.`);
    } catch (reason) {
      if (!isCurrent()) return;
      setError(reason instanceof ApiError && reason.code === "stale_export_revision"
        ? "That file changed after you opened this list. Check its current state before deleting it."
        : reason instanceof Error ? reason.message : "Unable to delete the published file.");
    } finally {
      if (isCurrent()) { setDeleting(null); setConfirming(null); void exports.refresh().catch(() => {}); }
    }
  };

  return <section className={additionalFiles ? "published-files workflow-published-files" : "published-files"} aria-labelledby="published-files-heading">
    {additionalFiles
      ? <h2 id="published-files-heading">Published files</h2>
      : <h3 id="published-files-heading">Published files</h3>}
    {notice && <p className="form-status" role="status">{notice}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {publications.length ? <ul>{publications.map((publication) => {
      const name = `${publication.format.toUpperCase()} · ${owner(publication)}`;
      return <li key={publication.id} aria-label={name}>
        <div>
          <strong>{name}</strong>
          <ExportConditionText publication={publication} source={publicationSource(publication, selectedId, reviews)} />
          <small>Published by {label(publication.actor_id)}. Policy: {label(publication.policy_owner_id)}, revision {publication.policy_revision}. Publication revision {publication.publication_revision}.</small>
        </div>
        {canDeletePublication(publication, me, allowed) && (publication.serving || publication.failed) && (confirming === publication.id
          ? <div className="button-row">
            <button type="button" className="btn-secondary compact-button" disabled={deleting !== null} onClick={() => setConfirming(null)}>Cancel</button>
            <button type="button" className="danger-button compact-button" disabled={deleting !== null} onClick={() => void remove(publication)}>
              {deleting === publication.id ? "Deleting…" : `Confirm delete ${publication.format.toUpperCase()}`}</button>
          </div>
          : <button type="button" className="danger-button compact-button" aria-label={`Delete ${name}`} disabled={deleting !== null}
            onClick={() => { setConfirming(publication.id); exports.setNotice(null); }}>Delete</button>)}
      </li>;
    })}</ul> : additionalFiles && !hasLegacy && <p className="muted-copy">No EDL or MCF files published.</p>}
    {hasLegacy && <div className="workflow-legacy-sidecars">
      <span className="muted-copy">Legacy sidecars</span>
      <ExportBadges status={{ exports: legacy }} />
    </div>}
    {additionalFiles}
  </section>;
}
