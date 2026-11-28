import { useState } from "react";
import {
  downloadReviewExport, preflightVideoSkipExport, type VideoSkipExportPreflight, type VideoSkipExportRequest,
} from "../api/api-exports";
import type { SavedReview } from "../api/api-review-types";
import { reviewPolicyToken } from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import { ModalDialog } from "../ui/ModalDialog";
import { saveExportFile } from "./export-download";
import { useReviewLifetime } from "./review-access";
import { formatReviewTimestamp, parseReviewTimestamp } from "./review-timestamps";

/** Check the selected cut and export the exact saved revision as a VideoSkip file. */
export function VideoSkipExportDialog({ review, onConflict, onClose }: {
  review: SavedReview;
  onConflict: (reason: unknown) => Promise<void>;
  onClose: () => void;
}) {
  const [timeText, setTimeText] = useState("");
  const [descriptionText, setDescriptionText] = useState("");
  const [preview, setPreview] = useState<VideoSkipExportPreflight | null>(null);
  const [working, setWorking] = useState<"preflight" | "download" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isCurrent = useReviewLifetime();
  const timestamp = timeText.trim();
  const description = descriptionText.trim();
  const hasReference = Boolean(timestamp || description);
  const seconds = parseReviewTimestamp(timestamp.includes(".") ? timestamp : `${timestamp}.000`, 3);
  const validReference = !hasReference || (Number.isFinite(seconds) && Boolean(description));
  const busy = working !== null;
  const canSave = preview?.eligible && preview.exported_count > 0;

  const resetPreview = () => { setPreview(null); setError(null); };
  const run = async (action: "preflight" | "download") => {
    if (busy || !validReference || (action === "download" && !canSave)) return;
    const request: VideoSkipExportRequest = {
      revision: review.revision, ...reviewPolicyToken(review),
      ...(hasReference ? { reference_seconds: seconds, reference_description: description } : {}),
    };
    setWorking(action);
    setError(null);
    try {
      if (action === "preflight") {
        const checked = await preflightVideoSkipExport(review.id, request);
        if (isCurrent()) setPreview(checked);
      } else {
        const { blob, filename } = await downloadReviewExport(review.id, "videoskip", request);
        if (isCurrent()) saveExportFile(blob, filename);
      }
    } catch (reason) {
      if (!isCurrent()) return;
      if (reason instanceof ApiError && (reason.code === "stale_review_revision" || reason.code === "stale_review_policy")) {
        onClose();
        await onConflict(reason);
        return;
      }
      if (action === "preflight") setPreview(null);
      setError(reason instanceof Error ? reason.message : "Unable to export VideoSkip.");
    } finally {
      if (isCurrent()) setWorking(null);
    }
  };

  return <ModalDialog labelledBy="videoskip-export-heading" className="export-dialog" onClose={busy ? undefined : onClose}>
    <h2 id="videoskip-export-heading">Export VideoSkip</h2>
    <p className="muted-copy">“{review.name}”, revision {review.revision}. The file uses the saved review's effective filters and timing for this media version.</p>
    <fieldset className="export-destinations" disabled={busy}>
      <legend>Sync reference (optional)</legend>
      <p className="muted-copy" id="videoskip-reference-help">Choose a distinctive point on this cut and describe what happens there.</p>
      <label className="field">
        <span>Reference time</span>
        <input type="text" placeholder="HH:MM:SS.SSS" value={timeText}
          aria-invalid={hasReference && !Number.isFinite(seconds) || undefined}
          aria-describedby={validReference ? "videoskip-reference-help" : "videoskip-reference-help videoskip-reference-error"}
          onChange={(event) => { setTimeText(event.target.value); resetPreview(); }} />
      </label>
      <label className="field">
        <span>Reference description</span>
        <input type="text" maxLength={256} value={descriptionText}
          aria-invalid={hasReference && !description || undefined}
          aria-describedby={validReference ? undefined : "videoskip-reference-error"}
          onChange={(event) => { setDescriptionText(event.target.value); resetPreview(); }} />
      </label>
      {!validReference && <p className="form-error" id="videoskip-reference-error">Enter both a description and a time in HH:MM:SS or HH:MM:SS.SSS format.</p>}
    </fieldset>
    {preview && <section className="export-section" aria-labelledby="videoskip-preview-heading">
      <h3 id="videoskip-preview-heading">Compatibility</h3>
      <p className="muted-copy" role="status">{preview.exported_count} of {preview.filter_count} selected filters can be written to this file.</p>
      {!canSave && <p className="form-error">This review can't be downloaded as a VideoSkip file.</p>}
      {preview.issues.length > 0 && <VideoSkipExportIssues issues={preview.issues} filters={review.filters} />}
      {preview.issue_count > preview.issues.length && <p className="muted-copy">Showing the first {preview.issues.length} of {preview.issue_count} compatibility issues.</p>}
    </section>}
    {error && <p className="load-error" role="alert">{error}</p>}
    <div className="button-row">
      <button type="button" className="btn-secondary" disabled={busy || !validReference} onClick={() => void run("preflight")}>
        {working === "preflight" ? "Checking…" : "Check compatibility"}
      </button>
      <button type="button" className="btn-primary" disabled={busy || !canSave} onClick={() => void run("download")}>
        {working === "download" ? "Downloading…" : "Download VideoSkip"}
      </button>
      <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Close</button>
    </div>
  </ModalDialog>;
}

function VideoSkipExportIssues({ issues, filters }: {
  issues: VideoSkipExportPreflight["issues"];
  filters: SavedReview["filters"];
}) {
  const filtersById = new Map(filters.map((filter) => [filter.id, filter]));
  return <ul className="videoskip-export-issues">
    {issues.map((issue, index) => {
      const filter = issue.filter_id ? filtersById.get(issue.filter_id) : undefined;
      const bounds = filter && [formatReviewTimestamp(filter.start_seconds, 3), formatReviewTimestamp(filter.end_seconds, 3)]
        .filter(Boolean).join(" – ");
      const cue = [filter?.label, bounds].filter(Boolean).join(" · ");
      return <li key={index}>
        {cue && <strong>{cue}. </strong>}{issue.omitted && <strong>Omitted. </strong>}{issue.message}
      </li>;
    })}
  </ul>;
}
