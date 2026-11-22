import { useState } from "react";
import { downloadReviewExport, publishReviewExport } from "../api/api-exports";
import type { ExportFormat, ExportPublication } from "../api/api-mcf-types";
import type { McfPreflightIssue, SavedReview, SavedReviewSummary } from "../api/api-review-types";
import { fetchMcfPreflight, reviewPolicyToken } from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { usePrincipal } from "../auth/auth-gate";
import { ModalDialog } from "../ui/ModalDialog";
import {
  EXPORT_FORMATS, canDownload, occupant, publishDestinations,
  type Allowed, type ExportDestination,
} from "./export-model";
import { ExportConditionText, publicationSource } from "./export-publication-view";
import type { VersionPublications } from "./export-publications";
import { useAccountLabels, useReviewLifetime } from "./review-access";

/** Where a publish attempt stands; a confirmation is bound to one format and destination. */
type Phase =
  | { kind: "idle" }
  | { kind: "working"; format: ExportFormat }
  | { kind: "confirm"; format: ExportFormat; confirmation: string; replaced: ExportPublication | undefined }
  | { kind: "stale"; format: ExportFormat };

const upper = (format: ExportFormat) => format.toUpperCase();

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Download or publish one saved review's EDL and MCF. Destinations come from
 * the caller's capabilities; each format's current occupant comes from
 * discovery. Replacing another review's file needs the server's confirmation,
 * and a stale confirmation replaces nothing.
 */
export function ExportDialog({ review, reviews, exports, allowed, formats = EXPORT_FORMATS, edlAvailable, onMcfIssues, onConflict, onPublished, onClose }: {
  review: SavedReview;
  reviews: SavedReviewSummary[];
  exports: VersionPublications;
  allowed: Allowed;
  formats?: readonly ExportFormat[];
  edlAvailable: boolean;
  onMcfIssues: (issues: McfPreflightIssue[]) => void;
  onConflict: (reason: unknown) => Promise<void>;
  onPublished: () => void;
  onClose: () => void;
}) {
  const me = usePrincipal()?.userId ?? null;
  const destinations = publishDestinations(review.owner_id, me, allowed);
  const [target, setTarget] = useState(destinations[0]?.target);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [message, setMessage] = useState<string | null>(null);
  const isCurrent = useReviewLifetime();
  const label = useAccountLabels([review.owner_id, review.policy_owner_id, ...exports.publications.flatMap((row) => [row.actor_id, row.target_user_id])]);
  const destination = destinations.find((candidate) => candidate.target === target);
  const busy = phase.kind === "working";
  const request = { revision: review.revision, ...reviewPolicyToken(review) };

  const destinationName = (value: ExportDestination) => value.target === "item_default"
    ? "Item default (shared fallback)"
    : value.target === "self" ? "Your personal files" : `Review owner's personal files (${label(value.userId)})`;

  const eligibleMcf = async () => {
    const preflight = await fetchMcfPreflight(review.id);
    if (!isCurrent()) return false;
    if (preflight.eligible) return true;
    onMcfIssues(preflight.issues);
    onClose();
    return false;
  };

  const fail = async (reason: unknown, fallback: string) => {
    if (reason instanceof ApiError && ["stale_review_revision", "stale_review_policy"].includes(reason.code ?? "")) {
      await onConflict(reason);
      onClose();
      return;
    }
    setMessage(reason instanceof Error ? reason.message : fallback);
  };

  const publish = async (format: ExportFormat, confirmation?: string) => {
    if (!destination || busy) return;
    setPhase({ kind: "working", format }); setMessage(null);
    try {
      if (format === "mcf" && !confirmation && !await eligibleMcf()) { setPhase({ kind: "idle" }); return; }
      const outcome = await publishReviewExport(review.id, format, { ...request, target: destination.target, confirmation });
      if (!isCurrent()) return;
      const listed = await exports.refresh();
      if (!isCurrent()) return;
      if (outcome.kind === "confirmation_required") {
        setPhase({ kind: "confirm", format, confirmation: outcome.confirmation, replaced: occupant(listed, destination, format) });
        return;
      }
      setPhase({ kind: "idle" });
      setMessage(`Published ${upper(format)} to ${destinationName(destination)}.`);
      onPublished();
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof ApiError && reason.code === "stale_export_confirmation") {
        await exports.refresh().catch(() => []);
        setPhase({ kind: "stale", format });
        return;
      }
      setPhase({ kind: "idle" });
      await fail(reason, `Unable to publish ${upper(format)}.`);
    }
  };

  const download = async (format: ExportFormat) => {
    if (busy) return;
    setPhase({ kind: "working", format }); setMessage(null);
    try {
      if (format === "mcf" && !await eligibleMcf()) return;
      const { blob, filename } = await downloadReviewExport(review.id, format, request);
      if (isCurrent()) saveBlob(blob, filename);
    } catch (reason) {
      if (isCurrent()) await fail(reason, `Unable to download ${upper(format)}.`);
    } finally { if (isCurrent()) setPhase((current) => current.kind === "working" ? { kind: "idle" } : current); }
  };

  const choose = (next: ExportDestination["target"]) => { setTarget(next); setPhase({ kind: "idle" }); setMessage(null); };
  const pending = phase.kind === "confirm" || phase.kind === "stale" ? phase : null;

  return <ModalDialog labelledBy="export-dialog-heading" className="export-dialog" onClose={busy ? undefined : onClose}>
    <h2 id="export-dialog-heading">Export</h2>
    <p className="muted-copy">“{review.name}”, revision {review.revision}. Filters follow the review owner's enforced policy ({label(review.policy_owner_id)}, policy revision {review.policy_revision}) for every destination.</p>
    {canDownload(review.owner_id, me, allowed) && <section className="export-section" aria-labelledby="export-download-heading">
      <h3 id="export-download-heading">Download for yourself</h3>
      <div className="button-row">{formats.map((format) => <button key={format} type="button" className="btn-secondary compact-button"
        disabled={busy || (format === "edl" && !edlAvailable)} title={format === "edl" && !edlAvailable ? "Include at least one scan before exporting EDL" : undefined}
        onClick={() => void download(format)}>Download {upper(format)}</button>)}</div>
    </section>}
    {destination && <section className="export-section" aria-labelledby="export-publish-heading">
      <h3 id="export-publish-heading">Publish beside the media</h3>
      <fieldset className="export-destinations" disabled={busy}>
        <legend>Destination</legend>
        {destinations.map((candidate) => <label key={candidate.target}>
          <input type="radio" name="export-destination" checked={candidate.target === target} onChange={() => choose(candidate.target)} />
          {destinationName(candidate)}
        </label>)}
      </fieldset>
      <p className="muted-copy">{destination.target === "item_default"
        ? "Jellyfin plays the item default for anyone without a personal file."
        : `Jellyfin plays these files only for ${label(destination.userId) === "You" ? "you" : label(destination.userId)}.`}</p>
      <ul className="export-format-list">{formats.map((format) => {
        const current = occupant(exports.publications, destination, format);
        return <li key={format} aria-label={`${upper(format)} at ${destinationName(destination)}`}>
          <div>
            <strong>{upper(format)}</strong>
            {current ? <ExportConditionText publication={current} source={publicationSource(current, review.id, reviews)} /> : <small>No file published here yet.</small>}
          </div>
          <button type="button" className="btn-primary compact-button" disabled={busy || Boolean(pending) || (format === "edl" && !edlAvailable)}
            onClick={() => void publish(format)}>{phase.kind === "working" && phase.format === format ? "Publishing…" : `Publish ${upper(format)}`}</button>
        </li>;
      })}</ul>
      {pending?.kind === "confirm" && <section className="export-confirmation" role="alert" aria-label="Confirm replacement">
        <h4>Replace {upper(pending.format)} at {destinationName(destination)}?</h4>
        <p>{pending.replaced
          ? <>The current file comes from {publicationSource(pending.replaced, review.id, reviews)} (publication revision {pending.replaced.publication_revision}, published by {label(pending.replaced.actor_id)}).</>
          : <>The current file comes from another saved review you cannot see.</>}
        {" "}Publishing replaces it with “{review.name}” revision {review.revision}. No saved review is deleted, and the other format is unchanged.</p>
        <div className="button-row">
          <button type="button" className="btn-secondary compact-button" onClick={() => setPhase({ kind: "idle" })}>Cancel</button>
          <button type="button" className="btn-primary compact-button" onClick={() => void publish(pending.format, pending.confirmation)}>Replace {upper(pending.format)}</button>
        </div>
      </section>}
      {pending?.kind === "stale" && <section className="export-confirmation" role="alert" aria-label="Replacement changed">
        <h4>The {upper(pending.format)} destination changed</h4>
        <p>Another change reached this destination after you reviewed it, so nothing was replaced. Retry publishes only if no other review's file is in the way; otherwise you are asked to confirm the current file.</p>
        <div className="button-row">
          <button type="button" className="btn-secondary compact-button" onClick={() => setPhase({ kind: "idle" })}>Cancel</button>
          <button type="button" className="btn-primary compact-button" onClick={() => void publish(pending.format)}>Retry {upper(pending.format)}</button>
        </div>
      </section>}
    </section>}
    {message && <p className="form-status" role="status">{message}</p>}
    <div className="button-row"><button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Close</button></div>
  </ModalDialog>;
}
