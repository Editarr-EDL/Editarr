import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  importEdlUpload,
  importMcfUpload,
  importVideoSkipUpload,
  acceptCreditsDetection,
  creditsDetectionClipUrl,
} from "../api/api-reviews";
import { ApiError } from "../api/api-transport";
import type { Scan } from "../api/api-scan-types";
import type {
  McfImportIssue,
  ReviewTargetRequest,
  AcceptedCreditsReference,
  SynchronizationEndMode,
} from "../api/api-review-types";
import type { CreditsAcceptanceContext, CreditsAcceptanceResult } from "../api/api-credits-types";
import {
  validateDisplayName,
} from "../shared/name-validation";
import {
  scanLabel,
} from "../shared/display";
import {
  ModalDialog,
} from "../ui/ModalDialog";
import {
  formatMcfTimestamp,
  parseMcfTimestamp,
} from "./review-mcf";
import type { ImportResult } from "./review-types";
import { useReviewAccess, useReviewLifetime } from "./review-access";

function ImportErrorMessage({ message, lineText }: { message: string; lineText: string }) {
  const [showDetails, setShowDetails] = useState(false);
  return <><span>{message}</span><span className="import-error-details"><button type="button" className="import-error-toggle" aria-expanded={showDetails} onClick={() => setShowDetails((visible) => !visible)}>{showDetails ? "Hide Details" : "Show Details"}</button>{showDetails && <code aria-label="Import error line">{lineText}</code>}</span></>;
}

function McfImportIssuesDialog({ issues, busy, onCancel, onContinue }: { issues: McfImportIssue[]; busy: boolean; onCancel: () => void; onContinue: () => void }) {
  return <ModalDialog labelledBy="mcf-import-issues-heading" className="modal-dialog" onClose={busy ? undefined : onCancel}>
    <h2 id="mcf-import-issues-heading">MCF import issues</h2>
    <p>Issues were encountered while reading this MCF file. You can cancel the import or continue anyway. Continuing ignores these issues and imports everything that can be read safely.</p>
    <ol className="mcf-import-issue-list">{issues.map((issue, index) => <li key={`${issue.line_number}:${issue.code}:${index}`}><p>{issue.message} (line {issue.line_number})</p><code>{issue.line_text}</code></li>)}</ol>
    <div className="button-row"><button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>Cancel</button><button type="button" className="btn-primary" onClick={onContinue} disabled={busy}>{busy ? "Importing…" : "Continue Anyway"}</button></div>
  </ModalDialog>;
}

/** Select a completed credits scan and accept its reviewed boundary into the supplied context. */
export function CreditsResultDialog({
  scans,
  context,
  disabled = false,
  onAccepted,
  onConflict,
}: {
  scans: Scan[];
  context: CreditsAcceptanceContext;
  disabled?: boolean;
  onAccepted: (result: CreditsAcceptanceResult) => void;
  onConflict?: (reason: unknown) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selectedScanId, setSelectedScanId] = useState("");
  const [boundary, setBoundary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const isCurrent = useReviewLifetime();
  const scan = scans.find((item) => item.id === selectedScanId) || scans[0] || null;
  const candidate = scan?.credits_detection?.result?.candidates.find((item) => item.id === scan.credits_detection?.result?.best_candidate_id) ?? null;

  const openDialog = () => {
    const nextScan = scans[0];
    const nextCandidate = nextScan?.credits_detection?.result?.candidates.find((item) => item.id === nextScan.credits_detection?.result?.best_candidate_id);
    setSelectedScanId(nextScan?.id || "");
    setBoundary(nextCandidate ? formatMcfTimestamp(nextCandidate.boundary_seconds) : "");
    setError(null);
    setOpen(true);
  };

  const selectScan = (scanId: string) => {
    const nextScan = scans.find((item) => item.id === scanId);
    const nextCandidate = nextScan?.credits_detection?.result?.candidates.find((item) => item.id === nextScan.credits_detection?.result?.best_candidate_id);
    setSelectedScanId(scanId);
    setBoundary(nextCandidate ? formatMcfTimestamp(nextCandidate.boundary_seconds) : "");
    setError(null);
  };

  const accept = async () => {
    if (!scan || !candidate || disabled || busy || !isCurrent()) return;
    const acceptedBoundary = parseMcfTimestamp(boundary);
    if (!Number.isFinite(acceptedBoundary)) {
      setError("Enter the credits boundary in HH:MM:SS.MMM format.");
      return;
    }
    setBusy(true); setError(null);
    try {
      const result = await acceptCreditsDetection(scan.id, { context, accepted_boundary_seconds: acceptedBoundary });
      if (!isCurrent()) return;
      onAccepted(result);
      setOpen(false);
    } catch (reason) {
      await onConflict?.(reason);
      setError(reason instanceof Error ? reason.message : "Unable to accept credits detection.");
    } finally { setBusy(false); }
  };

  const close = () => { if (!busy) setOpen(false); };
  return <>
    <button ref={trigger} type="button" className="btn-secondary" onClick={openDialog} disabled={disabled || scans.length === 0} title={!scans.length ? "Run a Credits scan from New Scan and wait for it to complete" : undefined}>Review Credits Result</button>
    {open && scan && candidate && <ModalDialog labelledBy="credits-result-heading" className="modal-dialog credits-detection-dialog" onClose={close}>
      <h2 id="credits-result-heading">Review Credits Result</h2>
      {scans.length > 1 && <label className="field"><span>Credits scan</span><select aria-label="Credits scan" value={scan.id} onChange={(event) => selectScan(event.target.value)} disabled={busy}>{scans.map((item) => <option key={item.id} value={item.id}>{scanLabel(item)}</option>)}</select></label>}
      <section className="credits-candidate" aria-labelledby="credits-candidate-heading"><h3 id="credits-candidate-heading">Proposed boundary</h3><p><strong>{Math.round(candidate.confidence * 100)}% confidence · {candidate.confidence_band}</strong> · {candidate.method.replace(/_/g, " ")}</p>{candidate.confidence_band === "low" && <p className="form-warning" role="status">Low confidence. Review the clip and timestamp before accepting.</p>}<video controls preload="metadata" src={creditsDetectionClipUrl(scan.id, parseMcfTimestamp(boundary) || candidate.boundary_seconds)} aria-label="Credits boundary preview" /><label className="field"><span>Credits begin</span><input aria-label="Accepted credits boundary" inputMode="numeric" value={boundary} onChange={(event) => setBoundary(event.target.value)} /></label><div className="button-row"><button type="button" className="btn-secondary" onClick={close} disabled={busy}>Dismiss</button><button type="button" className="btn-primary" onClick={() => void accept()} disabled={busy || disabled}>{busy ? "Accepting…" : "Accept boundary"}</button></div></section>
      {error && <p className="form-error" role="alert">{error}</p>}
    </ModalDialog>}
  </>;
}

/** Edit source and end timestamps used to align an imported MCF file.
 * @param props Timing values, pending state, and change callbacks.
 */
function McfImportTimingFields({
  startTimestamp,
  beforeCreditsEnd,
  fileEnd,
  endMode,
  busy,
  onStartChange,
  onBeforeCreditsEndChange,
  onFileEndChange,
  onEndModeChange,
}: {
  startTimestamp: string;
  beforeCreditsEnd: string;
  fileEnd: string;
  endMode: SynchronizationEndMode;
  busy: boolean;
  onStartChange: (value: string) => void;
  onBeforeCreditsEndChange: (value: string) => void;
  onFileEndChange: (value: string) => void;
  onEndModeChange: (value: SynchronizationEndMode) => void;
}) {
  return <fieldset className="mcf-import-timing"><legend>Target cue timing</legend>
    <label className="field"><span>Target START</span><input aria-label="Target START" inputMode="numeric" placeholder="HH:MM:SS.MMM" value={startTimestamp} onChange={(event) => onStartChange(event.target.value)} disabled={busy} /></label>
    <div className="segmented-control" role="group" aria-label="Target END meaning"><button type="button" className={endMode === "before_credits" ? "selected" : ""} aria-pressed={endMode === "before_credits"} onClick={() => onEndModeChange("before_credits")} disabled={busy}>Before credits</button><button type="button" className={endMode === "file_end" ? "selected" : ""} aria-pressed={endMode === "file_end"} onClick={() => onEndModeChange("file_end")} disabled={busy}>File end</button></div>
    {endMode === "before_credits"
      ? <label className="field"><span>Before-credits END</span><input aria-label="Before-credits END" inputMode="numeric" placeholder="HH:MM:SS.MMM" value={beforeCreditsEnd} onChange={(event) => onBeforeCreditsEndChange(event.target.value)} disabled={busy} /><small>Maps cues to the end of the main material and becomes the canonical MCF END.</small></label>
      : <><label className="field"><span>File END</span><input aria-label="File END" inputMode="numeric" placeholder="HH:MM:SS.MMM" value={fileEnd} onChange={(event) => onFileEndChange(event.target.value)} disabled={busy} /><small>Maps cues to the physical file end.</small></label><label className="field"><span>Canonical before-credits END</span><input aria-label="Canonical before-credits END" inputMode="numeric" placeholder="HH:MM:SS.MMM (optional)" value={beforeCreditsEnd} onChange={(event) => onBeforeCreditsEndChange(event.target.value)} disabled={busy} /><small>Required later for MCF export; this is separate from File END.</small></label></>}
  </fieldset>;
}

const importFormats = {
  edl: { label: "EDL", accept: ".edl,text/plain", description: "Simple EDL (.edl)" },
  mcf: { label: "MCF", accept: ".mcf,text/plain,text/mcf+vtt", description: "Movie Content Filter 1.0.0, 1.1.0, or 1.1.1 (.mcf)" },
  videoskip: { label: "VideoSkip", accept: ".skp,text/plain", description: "VideoSkip (.skp). Verify timing on the selected cut before using the cues." },
};

/** Import a filter file for the selected Jellyfin version. */
export function ImportDialog({
  target,
  itemName = null,
  versionName = null,
  durationSeconds,
  creditsScans,
  onImported,
  stageForVerification = false,
  disabled = false,
}: {
  target: ReviewTargetRequest;
  itemName?: string | null;
  versionName?: string | null;
  durationSeconds: number | null;
  creditsScans: Scan[];
  onImported?: (result: ImportResult) => void;
  stageForVerification?: boolean;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<keyof typeof importFormats>("edl");
  const [sourceUrl, setSourceUrl] = useState("");
  const [uploader, setUploader] = useState("");
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ReactNode>(null);
  const [importIssues, setImportIssues] = useState<McfImportIssue[]>([]);
  const [startTimestamp, setStartTimestamp] = useState("00:00:00.000");
  const [beforeCreditsEnd, setBeforeCreditsEnd] = useState("");
  const [fileEnd, setFileEnd] = useState(() => formatMcfTimestamp(durationSeconds));
  const [endMode, setEndMode] = useState<SynchronizationEndMode>("before_credits");
  const [acceptedCredits, setAcceptedCredits] = useState<AcceptedCreditsReference | null>(null);
  const nameValidation = validateDisplayName(name, true);
  const { label: formatLabel, accept, description } = importFormats[format];
  const trigger = useRef<HTMLButtonElement>(null);
  const access = useReviewAccess(null);
  const mayUpload = access.allowed("reviews.import.upload");
  const isCurrent = useReviewLifetime();
  useEffect(() => setFileEnd(formatMcfTimestamp(durationSeconds)), [durationSeconds]);
  const close = () => {
    if (busy) return;
    setOpen(false); setError(null); setImportIssues([]);
  };
  const submit = async (ignoreErrors = false) => {
    if (busy || disabled || !isCurrent() || !mayUpload) return;
    if (nameValidation.error || !nameValidation.normalized || !file) {
      setError(nameValidation.error || `Enter a review name and choose a file for ${formatLabel}.`);
      return;
    }
    const startSeconds = parseMcfTimestamp(startTimestamp);
    const endTimestamp = endMode === "before_credits" ? beforeCreditsEnd : fileEnd;
    const endSeconds = parseMcfTimestamp(endTimestamp);
    const materialEndSeconds = beforeCreditsEnd ? parseMcfTimestamp(beforeCreditsEnd) : null;
    if (!stageForVerification && format === "mcf" && (!Number.isFinite(startSeconds) || !Number.isFinite(endSeconds) || endSeconds <= startSeconds || (materialEndSeconds !== null && (!Number.isFinite(materialEndSeconds) || materialEndSeconds <= startSeconds || materialEndSeconds > endSeconds)))) {
      setError("Enter valid target times in HH:MM:SS.MMM format. END must be after START and within the selected file interval.");
      return;
    }
    setBusy(true); setError(null);
    try {
      const options = { name: nameValidation.normalized, target,
        ...(stageForVerification ? { stage_for_verification: true } : {}) };
      const mcfOptions = {
        ...options,
        ignore_errors: ignoreErrors,
        synchronization: stageForVerification
          ? { start_seconds: 0, end_seconds: durationSeconds && durationSeconds > 0 ? durationSeconds : 1, end_mode: "file_end" as const }
          : { start_seconds: startSeconds, end_seconds: endSeconds, end_mode: endMode },
        mcf_material_end_seconds: stageForVerification ? null : endMode === "before_credits" ? endSeconds : materialEndSeconds,
        accepted_credits: acceptedCredits,
      };
      const result = await {
        edl: () => importEdlUpload(file, options),
        mcf: () => importMcfUpload(file, mcfOptions),
        videoskip: () => importVideoSkipUpload(file, {
          name: options.name, target,
          attribution: { source_url: sourceUrl.trim() || null, uploader: uploader.trim() || null },
        }),
      }[format]();
      if (!isCurrent()) return;
      onImported?.(result);
      setSourceUrl(""); setUploader("");
      setName(""); setFile(null); setImportIssues([]); setStartTimestamp("00:00:00.000"); setBeforeCreditsEnd(""); setFileEnd(formatMcfTimestamp(durationSeconds)); setEndMode("before_credits"); setAcceptedCredits(null); setOpen(false);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : `Unable to import ${formatLabel}.`;
      if (reason instanceof ApiError && reason.code === "mcf_import_issues" && reason.importIssues.length > 0) {
        setOpen(false);
        setImportIssues(reason.importIssues);
      } else {
        setImportIssues([]);
        setOpen(true);
        setError(reason instanceof ApiError && reason.lineText !== null
          ? <ImportErrorMessage message={message} lineText={reason.lineText} />
          : message);
      }
    } finally { setBusy(false); }
  };
  if (!mayUpload) return null;
  return <>
    <button ref={trigger} type="button" className="btn-secondary" onClick={() => setOpen(true)} disabled={disabled}>Import</button>
    {importIssues.length > 0 && <McfImportIssuesDialog issues={importIssues} busy={busy} onCancel={close} onContinue={() => void submit(true)} />}
    {open && <ModalDialog labelledBy="import-heading" className="modal-dialog" onClose={busy ? undefined : close}>
      <h2 id="import-heading">Import</h2>
      <p>Imports into Jellyfin item {itemName || target.item_id}{target.media_source_id ? `, version ${versionName || target.media_source_id}` : ""}</p>
      <div className="segmented-control" role="group" aria-label="Import format">
        {(Object.keys(importFormats) as (keyof typeof importFormats)[]).map((choice) =>
          <button key={choice} type="button" className={format === choice ? "selected" : ""} aria-pressed={format === choice}
            onClick={() => { setFormat(choice); setFile(null); setError(null); }} disabled={busy}>{importFormats[choice].label}</button>)}
      </div>
      <p className="muted-copy">{description}</p>
      {format === "mcf" && !stageForVerification && <McfImportTimingFields startTimestamp={startTimestamp} beforeCreditsEnd={beforeCreditsEnd} fileEnd={fileEnd} endMode={endMode} busy={busy} onStartChange={setStartTimestamp} onBeforeCreditsEndChange={setBeforeCreditsEnd} onFileEndChange={setFileEnd} onEndModeChange={setEndMode} />}
      {format === "mcf" && !stageForVerification && <div className="mcf-detect-row"><CreditsResultDialog scans={creditsScans} context={{ kind: "import", target }} disabled={busy} onAccepted={(result) => { setAcceptedCredits(result.accepted_credits); setBeforeCreditsEnd(formatMcfTimestamp(result.accepted_credits.accepted_boundary_seconds)); }} />{acceptedCredits && <span className="form-status" role="status">Accepted credits boundary {formatMcfTimestamp(acceptedCredits.accepted_boundary_seconds)}</span>}</div>}
      <label className="field"><span>Review name</span><input aria-label="Import review name" value={name} aria-invalid={Boolean(name && nameValidation.error)} onChange={(event) => setName(event.target.value)} disabled={busy} /></label>
      {format === "videoskip" && <fieldset className="mcf-import-timing"><legend>Source attribution (optional)</legend>
        <label className="field"><span>Exchange file URL</span><input type="url" value={sourceUrl} maxLength={512} onChange={(event) => setSourceUrl(event.target.value)} disabled={busy} /></label>
        <label className="field"><span>Uploader</span><input value={uploader} maxLength={120} onChange={(event) => setUploader(event.target.value)} disabled={busy} /></label>
      </fieldset>}
      <p className="muted-copy">Created scan: {nameValidation.normalized || "Enter a review name"}</p>
      <label className="field"><span>{formatLabel} file</span><input key={format} aria-label={`${formatLabel} file`} type="file" accept={accept} onChange={(event) => setFile(event.target.files?.[0] || null)} disabled={busy} /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="button-row"><button type="button" className="btn-secondary" onClick={close} disabled={busy}>Cancel</button><button type="button" className="btn-primary" onClick={() => void submit()} disabled={busy}>{busy ? "Importing…" : `Import ${formatLabel}`}</button></div>
    </ModalDialog>}
  </>;
}
