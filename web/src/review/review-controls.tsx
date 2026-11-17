import {
  useEffect,
  useState,
} from "react";
import { fetchMcfRegistry } from "../api/api-service";
import { retimeSavedReview, reviewPolicyToken } from "../api/api-reviews";
import { useReviewLifetime } from "./review-access";
import type { PreviewFilter } from "./review-preview";
import type {
  ExportFormat,
  ExportStatus,
  McfRegistry,
} from "../api/api-mcf-types";
import type {
  NewManualReviewFilter,
  SynchronizationEndMode,
} from "../api/api-review-types";
import {
  createReviewDraft,
  reviewDraftFilters,
} from "./review-draft";
import {
  ModalDialog,
} from "../ui/ModalDialog";
import {
  exportProvenance,
  legacyExports,
} from "./export-status";
import {
  McfClassificationFields,
  classificationIsValid,
  formatMcfTimestamp,
  parseMcfTimestamp,
} from "./review-mcf";

/** Collect a manually authored filter for the current review draft.
 * @param onClose Dismisses the dialog.
 * @param onAdd Stages the completed filter.
 */
export function ManualReviewFilterDialog({
  onClose,
  onAdd,
  disabled = false,
  onPreview,
  preview,
  previewError,
}: {
  onClose: () => void;
  onAdd: (filter: NewManualReviewFilter) => void;
  disabled?: boolean;
  onPreview?: (filter: PreviewFilter) => void;
  preview?: { filterId: string; url: string } | null;
  previewError?: string | null;
}) {
  const [registry, setRegistry] = useState<McfRegistry | null>(null);
  const [source, setSource] = useState<"audio" | "visual">("audio");
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState<NonNullable<NewManualReviewFilter["category"]>>("language");
  const [subcategory, setSubcategory] = useState("swearing");
  const [severity, setSeverity] = useState<NewManualReviewFilter["severity"]>(null);
  const [channel, setChannel] = useState<NewManualReviewFilter["mcf_channel"]>(null);
  const [comment, setComment] = useState<NewManualReviewFilter["mcf_comment"]>(null);
  const [details, setDetails] = useState("");
  const [startSeconds, setStartSeconds] = useState(0);
  const [endSeconds, setEndSeconds] = useState(1);
  useEffect(() => { void fetchMcfRegistry().then(setRegistry); }, []);
  const invalidTiming = !Number.isFinite(startSeconds) || !Number.isFinite(endSeconds)
    || startSeconds < 0 || endSeconds <= startSeconds;
  const action = source === "audio" ? "mute" : "skip";
  const previewId = `manual-preview:${source}:${startSeconds}:${endSeconds}`;
  const invalid = disabled || !label.trim() || invalidTiming
    || !classificationIsValid(registry, category, subcategory)
    || Boolean(comment?.includes("-->"));
  return <ModalDialog labelledBy="manual-review-filter-heading" className="finding-editor" onClose={onClose}>
    <h2 id="manual-review-filter-heading">Add Manual Filter</h2>
    <p>This filter will be created with the saved review when changes are saved.</p>
    <label className="field"><span>Filter type</span><select aria-label="Manual filter type" value={source} onChange={(event) => { const next = event.target.value as "audio" | "visual"; setSource(next); setCategory(next === "audio" ? "language" : "violence"); setSubcategory(next === "audio" ? "swearing" : "violence"); }}><option value="audio">Audio mute</option><option value="visual">Video skip</option></select></label>
    <label className="field"><span>{source === "audio" ? "Word or label" : "Label"}</span><input aria-label="Manual filter label" value={label} onChange={(event) => setLabel(event.target.value)} /></label>
    {registry ? <McfClassificationFields controlId="Manual filter" registry={registry} value={{ category, subcategory, severity, mcf_channel: channel, mcf_comment: comment }} onChange={(patch) => { if (patch.category !== undefined) setCategory(patch.category || "language"); if (patch.subcategory !== undefined) setSubcategory(patch.subcategory || ""); if (patch.severity !== undefined) setSeverity(patch.severity); if (patch.mcf_channel !== undefined) setChannel(patch.mcf_channel); if (patch.mcf_comment !== undefined) setComment(patch.mcf_comment); }} /> : <p className="loading-state">Loading MCF categories…</p>}
    <label className="field"><span>Description</span><textarea aria-label="Manual filter description" value={details} onChange={(event) => setDetails(event.target.value)} rows={3} /></label>
    <div className="finding-timing-fields"><label className="field"><span>Start seconds</span><input aria-label="Manual filter start seconds" type="number" min="0" step="0.1" value={startSeconds} onChange={(event) => setStartSeconds(Number(event.target.value))} /></label><label className="field"><span>End seconds</span><input aria-label="Manual filter end seconds" type="number" min="0.1" step="0.1" value={endSeconds} onChange={(event) => setEndSeconds(Number(event.target.value))} /></label></div>
    {preview?.filterId === previewId && <div className="finding-preview">{source === "audio"
      ? <audio controls src={preview.url} aria-label="Manual audio preview" />
      : <video controls src={preview.url} aria-label="Manual video preview" />}</div>}
    {previewError && <p className="form-error" role="alert">{previewError}</p>}
    <div className="button-row">{onPreview && <button type="button" className="btn-secondary"
      disabled={disabled || invalidTiming} onClick={() => onPreview({
        id: previewId, source, edit_action: action, start_seconds: startSeconds, end_seconds: endSeconds,
      })}>Preview</button>}<button type="button" className="btn-secondary" onClick={onClose}>Cancel</button><button type="button" className="btn-primary" disabled={invalid} onClick={() => onAdd({ id: "", source, category, subcategory, severity, mcf_channel: channel, mcf_comment: comment, label: label.trim(), details: details.trim() || null, group_key: null, start_seconds: startSeconds, end_seconds: endSeconds, confidence: null, edit_action: action, selected: true })}>Add Filter</button></div>
  </ModalDialog>;
}

export function ManageExportsDialog({
  status,
  subject,
  deleting,
  onDelete,
  onClose,
}: {
  status: ExportStatus;
  subject: string;
  deleting: ExportFormat | null;
  onDelete: (format: ExportFormat) => void;
  onClose: () => void;
}) {
  const [confirming, setConfirming] = useState<ExportFormat | null>(null);
  const active = ((["edl", "mcf"] as const).map((format) => legacyExports(status)[format])).filter((item) => item.present);
  return <ModalDialog labelledBy="manage-exports-heading" className="confirmation-dialog" onClose={deleting ? undefined : onClose}><h2 id="manage-exports-heading">Manage exports</h2><p>Sidecars beside <strong>{subject}</strong> are managed independently.</p><div className="managed-export-list">{active.map((item) => <section key={item.format} aria-label={`${item.format.toUpperCase()} export`}><div><strong>{item.format.toUpperCase()} · {item.outdated ? "Outdated" : "Current"}</strong><small>{item.exported_at ? new Date(item.exported_at).toLocaleString() : "Export time unavailable"}</small><small>{item.exported_review ? `Review: ${item.exported_review.name}` : "Review attribution unavailable"}</small><small>{exportProvenance(item.exported_scans)}</small></div>{confirming === item.format ? <div className="button-row"><button type="button" className="btn-secondary compact-button" onClick={() => setConfirming(null)} disabled={Boolean(deleting)}>Cancel</button><button type="button" className="danger-button compact-button" onClick={() => onDelete(item.format)} disabled={Boolean(deleting)}>{deleting === item.format ? "Deleting…" : `Confirm Delete ${item.format.toUpperCase()}`}</button></div> : <button type="button" className="danger-button compact-button" onClick={() => setConfirming(item.format)} disabled={Boolean(deleting)}>Delete {item.format.toUpperCase()}</button>}</section>)}</div><div className="button-row"><button type="button" className="btn-secondary" onClick={onClose} disabled={Boolean(deleting)}>Close</button></div></ModalDialog>;
}

/** Edit cue synchronization and apply a validated retime result.
 * @param props Review, draft, disabled state, and apply callback.
 */
export function AdjustCueTimesDialog({
  reviewId,
  draft,
  disabled,
  onApply,
  onConflict,
}: {
  reviewId: string;
  draft: ReturnType<typeof createReviewDraft>;
  disabled: boolean;
  onApply: (result: Awaited<ReturnType<typeof retimeSavedReview>>) => void;
  onConflict?: (reason: unknown) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [materialEnd, setMaterialEnd] = useState("");
  const [mode, setMode] = useState<SynchronizationEndMode>(draft.synchronization.end_mode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isCurrent = useReviewLifetime();
  const openDialog = () => {
    setStart(formatMcfTimestamp(draft.synchronization.start_seconds));
    setEnd(formatMcfTimestamp(draft.synchronization.end_seconds));
    setMaterialEnd(formatMcfTimestamp(draft.mcfDocument.material_end_seconds));
    setMode(draft.synchronization.end_mode);
    setError(null); setOpen(true);
  };
  const apply = async () => {
    if (disabled || busy || !isCurrent()) return;
    const startSeconds = parseMcfTimestamp(start);
    const endSeconds = parseMcfTimestamp(end);
    const materialEndSeconds = materialEnd ? parseMcfTimestamp(materialEnd) : null;
    if (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds || (mode === "file_end" && materialEndSeconds !== null && (!Number.isFinite(materialEndSeconds) || materialEndSeconds <= startSeconds || materialEndSeconds > endSeconds))) {
      setError("Enter valid ordered times in HH:MM:SS.MMM format.");
      return;
    }
    setBusy(true); setError(null);
    try {
      const result = await retimeSavedReview(reviewId, {
        ...reviewPolicyToken(draft),
        revision: draft.revision,
        memberships: draft.memberships,
        current_synchronization: draft.synchronization,
        proposed_synchronization: { start_seconds: startSeconds, end_seconds: endSeconds, end_mode: mode },
        mcf_document: draft.mcfDocument,
        filters: reviewDraftFilters(draft),
        filter_basis: "base",
        accepted_credits: draft.acceptedCredits,
        new_manual_filters: draft.newManualFilters,
      });
      if (!isCurrent()) return;
      onApply(mode === "file_end" ? { ...result, mcf_document: { ...result.mcf_document, material_end_seconds: materialEndSeconds } } : result);
      setOpen(false);
    } catch (reason) {
      await onConflict?.(reason);
      setError(reason instanceof Error ? reason.message : "Unable to adjust cue times.");
    } finally { setBusy(false); }
  };
  const times = [...draft.filters, ...draft.newManualFilters].flatMap((filter) => [filter.start_seconds, filter.end_seconds]).filter((value): value is number => typeof value === "number");
  return <>
    <button type="button" className="btn-secondary" onClick={openDialog} disabled={disabled}>Adjust Cue Times</button>
    {open && <ModalDialog labelledBy="adjust-cue-times-heading" className="modal-dialog" onClose={busy ? undefined : () => setOpen(false)}>
      <h2 id="adjust-cue-times-heading">Adjust Cue Times</h2>
      <p>Only imported or unverified cues are adjusted. Verified local scan cues and staged manual cues keep their times.</p>
      <dl className="timing-summary"><div><dt>Current START</dt><dd>{formatMcfTimestamp(draft.synchronization.start_seconds)}</dd></div><div><dt>Current END</dt><dd>{formatMcfTimestamp(draft.synchronization.end_seconds)} · {draft.synchronization.end_mode === "before_credits" ? "Before credits" : "File end"}</dd></div></dl>
      <p>{times.length ? `${draft.filters.length + draft.newManualFilters.length} cues spanning ${formatMcfTimestamp(Math.min(...times))}–${formatMcfTimestamp(Math.max(...times))}` : "No cues are currently staged."}</p>
      <label className="field"><span>Proposed START</span><input aria-label="Proposed START" inputMode="numeric" value={start} onChange={(event) => setStart(event.target.value)} disabled={busy} /></label>
      <div className="segmented-control" role="group" aria-label="Proposed END meaning"><button type="button" className={mode === "before_credits" ? "selected" : ""} aria-pressed={mode === "before_credits"} onClick={() => setMode("before_credits")} disabled={busy}>Before credits</button><button type="button" className={mode === "file_end" ? "selected" : ""} aria-pressed={mode === "file_end"} onClick={() => setMode("file_end")} disabled={busy}>File end</button></div>
      <label className="field"><span>{mode === "before_credits" ? "Proposed before-credits END" : "Proposed file END"}</span><input aria-label="Proposed synchronization END" inputMode="numeric" value={end} onChange={(event) => setEnd(event.target.value)} disabled={busy} /></label>
      {mode === "file_end" && <label className="field"><span>Canonical before-credits END</span><input aria-label="Proposed canonical before-credits END" inputMode="numeric" value={materialEnd} onChange={(event) => setMaterialEnd(event.target.value)} disabled={busy} /></label>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="button-row"><button type="button" className="btn-secondary" onClick={() => setOpen(false)} disabled={busy}>Cancel</button><button type="button" className="btn-primary" onClick={() => void apply()} disabled={busy || disabled}>{busy ? "Adjusting…" : "Apply to draft"}</button></div>
    </ModalDialog>}
  </>;
}
