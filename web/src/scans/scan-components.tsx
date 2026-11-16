import {
  useEffect,
  useRef,
  useState,
} from "react";
import { fetchScanReferences, renameScan } from "../api/api-scans";
import { audioTrackLabel } from "./audio-track-field";
import { ShareAction } from "./sharing-dialog";
import { usePrincipal } from "../auth/auth-gate";
import { scanActionAllowed } from "./scan-access";
import type {
  Scan,
  ScanMatch,
  ScanQueueItem,
} from "../api/api-scan-types";
import {
  validateDisplayName,
} from "../shared/name-validation";
import {
  formatTime,
  scanLabel,
  scanOriginLabel,
} from "../shared/display";
import {
  ModalDialog,
} from "../ui/ModalDialog";

export function formatScanDuration(scan: Scan | ScanQueueItem, now = Date.now()): string {
  if (typeof scan.scan_duration_seconds === "number") return formatTime(scan.scan_duration_seconds);
  if (scan.manual) return "N/A";
  const isInProgress = ["scanning", "orphaned", "cancelling"].includes(scan.status);
  const start = scan.scan_started_at || (isInProgress ? scan.created_at : null);
  if (!start) return scan.status === "queued" ? "Not started" : "Unavailable";
  const startedAt = new Date(start).getTime();
  if (Number.isNaN(startedAt)) return "Unavailable";
  const endedAt = isInProgress ? now : new Date(scan.updated_at).getTime();
  if (Number.isNaN(endedAt)) return "Unavailable";
  return formatTime((endedAt - startedAt) / 1000);
}

function scanIsActive(scan: ScanQueueItem, now = Date.now()): boolean {
  if (scan.status !== "scanning") return false;
  const updatedAt = new Date(scan.updated_at).getTime();
  return !Number.isNaN(updatedAt) && now - updatedAt <= 5_000;
}

function ScanActivityIndicator({ scan, now }: { scan: ScanQueueItem; now: number }) {
  const active = scanIsActive(scan, now);
  return <span className={`scan-activity ${active ? "active" : "stalled"}`} role="status">{active ? "Active" : "Stalled"}</span>;
}

/** Display a scan's elapsed time, updating while it is active.
 * @param scan Scan or queued scan with timing fields.
 */
export function ScanDuration({ scan }: { scan: Scan | ScanQueueItem }) {
  const isInProgress = ["scanning", "orphaned", "cancelling"].includes(scan.status);
  const [now, setNow] = useState(() => Date.now());
  const [receivedAt, setReceivedAt] = useState(() => Date.now());

  useEffect(() => {
    setReceivedAt(Date.now());
  }, [scan.id, scan.scan_duration_seconds, scan.status, scan.updated_at]);

  useEffect(() => {
    if (!isInProgress) return;
    setNow(Date.now());
    const interval = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(interval);
  }, [isInProgress]);

  const elapsedSinceReceipt = isInProgress ? Math.max(0, (now - receivedAt) / 1000) : 0;
  const duration = typeof scan.scan_duration_seconds === "number"
    ? formatTime(scan.scan_duration_seconds + elapsedSinceReceipt)
    : formatScanDuration(scan, now);
  return <time dateTime={scan.scan_started_at || undefined}>{duration}</time>;
}

export function ScanProgressDetails({ scan, now }: { scan: ScanQueueItem; now: number }) {
  if (scan.progress_percent === null || scan.progress_percent === undefined) return null;
  return <div className="queue-progress">
    <div className="queue-progress-main">
      <progress value={scan.progress_percent} max="100" aria-label={`${scanLabel(scan)} progress`} />
      <span>{Math.round(scan.progress_percent)}%</span>
      {scan.status === "scanning" && <ScanActivityIndicator scan={scan} now={now} />}
    </div>
    {(scan.progress_message || scan.progress_stage) && <span className="queue-progress-detail">{scan.progress_message || scan.progress_stage}</span>}
  </div>;
}

function scanAnalysisDetails(scan: Scan | ScanQueueItem): string {
  if (scan.manual) return "Human-authored audio and video filters";
  if (scan.scan_mode === "credits_detection") {
    const candidate = scan.credits_detection?.result?.candidates[0];
    return candidate
      ? `End Credits · ${candidate.method.replace(/_/g, " ")} · ${Math.round(candidate.confidence * 100)}% confidence (${candidate.confidence_band})`
      : `End Credits · ${scan.credits_detection?.detector_version || "credits detector"}`;
  }
  const analyses = [
    scan.scan_audio ? `${scan.audio_model || "whisper"} transcriber · ${scan.audio_filter || "better_profanity"} filter` : null,
    scan.scan_audio ? (scan.analyzed_audio_selection
      ? `Analyzed audio: ${audioTrackLabel(scan.analyzed_audio_selection.track)}`
      : scan.requested_audio_selection && ["queued", "scanning", "orphaned", "cancelling"].includes(scan.status)
        ? `Requested audio: ${audioTrackLabel(scan.requested_audio_selection.track)}`
        : "Analyzed audio track unknown") : null,
    scan.scan_video ? `${scan.video_model || "Video"} video` : null,
  ].filter(Boolean).join(" · ");
  const videoOptions = scan.scan_video
    ? ` · Fine scan every frame${scan.coarse_scan_enabled ? ` · Coarse scan ${scan.coarse_scan_interval || 5}s` : ""} · ${scan.qwen_model?.includes("-4b-") ? "Qwen3-VL 4B" : "Qwen3-VL 8B"} · ${scan.qwen_summaries_per_segment} representative frames · Join gaps ${scan.qwen_segment_join_gap_seconds}s`
    : "";
  return `${analyses || "No analyses"}${videoOptions}`;
}

export function scanOptionsSummary(scan: Scan | ScanQueueItem): string {
  const mcf = scan.mcf_document
    ? ` · MCF ${scan.mcf_document.source_version} · ${scan.mcf_document.title || "Untitled"} · ${scan.mcf_document.material_start_seconds}–${scan.mcf_document.material_end_seconds}s`
    : "";
  const coverage = scan.stage_coverage?.length
    ? scan.stage_coverage.map((stage) => `${stage.stage}: ${stage.status}`).join(", ")
    : "Unknown coverage";
  return `${scanAnalysisDetails(scan)}${scan.media_source_name ? ` · ${scan.media_source_name}` : ""}${mcf} · ${coverage} · Scanner ${scan.scanner_behavior_version || "unknown"}`;
}

export function ScanOptionsInfo({ scan }: { scan: Scan | ScanQueueItem }) {
  const [visible, setVisible] = useState(false);
  return <span className="scan-options-info" onMouseEnter={() => setVisible(true)} onMouseLeave={() => setVisible(false)}>
    <button
      className="scan-options-button"
      type="button"
      aria-label={`Show options for ${scanLabel(scan)}`}
      aria-expanded={visible}
      onClick={() => setVisible((value) => !value)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
    >i</button>
    {visible && <span className="scan-options-tooltip" role="tooltip"><strong>Scan options</strong><span>{scanOptionsSummary(scan)} · {scanOriginLabel(scan)}</span></span>}
  </span>;
}

/** Edit a scan name and report a successful save.
 * @param scan Scan to rename.
 * @param onRenamed Refresh callback after saving.
 */
function ScanRenameAction({ scan, onRenamed }: { scan: Scan; onRenamed?: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(scan.name || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const validation = validateDisplayName(value, false);
  const close = () => {
    setEditing(false);
    setError(null);
    window.requestAnimationFrame(() => trigger.current?.focus());
  };
  const submit = async () => {
    if (validation.error) { setError(validation.error); return; }
    setSaving(true); setError(null);
    try {
      await renameScan(scan.id, validation.normalized);
      setEditing(false);
      onRenamed?.();
      window.requestAnimationFrame(() => trigger.current?.focus());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to rename scan.");
    } finally { setSaving(false); }
  };
    if (!editing) return <button ref={trigger} type="button" className="icon-button saved-review-rename-button" aria-label={`Rename ${scanLabel(scan)}`} title={`Rename ${scanLabel(scan)}`} onClick={() => { setValue(scan.name || ""); setEditing(true); }}>✎</button>;
  return <span className="scan-rename-editor"><label><span className="sr-only">Name for {scanLabel(scan)}</span><input autoFocus value={value} onChange={(event) => setValue(event.target.value)} disabled={saving} aria-invalid={Boolean(validation.error)} /></label><button type="button" className="btn-primary compact-button" onClick={() => void submit()} disabled={saving || Boolean(validation.error)}>{saving ? "Saving…" : "Save"}</button><button type="button" className="btn-secondary compact-button" onClick={close} disabled={saving}>Cancel</button>{(error || validation.error) && <span role="alert">{error || validation.error}</span>}</span>;
}

/** Show scan evidence and optional review selection controls for the supplied scans. */
export function ScanSelectionList({
  scans,
  onToggleScan,
  onDeleteScan,
  onRenamed,
  selectedAudioScanIds = [],
  selectedVideoScanIds = [],
  matchReasons = {},
  label = "Scan history",
}: {
  scans: Scan[];
  selectedAudioScanIds?: string[];
  selectedVideoScanIds?: string[];
  onToggleScan?: (scan: Scan, enabled: boolean) => void;
  onDeleteScan: (scan: Scan) => void;
  onRenamed?: () => void;
  matchReasons?: Record<string, ScanMatch["match_reason"]>;
  label?: string;
}) {
  const principal = usePrincipal();
  const scanIsSelected = (scan: Scan) => {
    const applicableSelections = [
      scan.scan_audio ? selectedAudioScanIds.includes(scan.id) : true,
      scan.scan_video ? selectedVideoScanIds.includes(scan.id) : true,
    ];
    return applicableSelections.every(Boolean);
  };
  return <div className="bounded-scan-list" role="list" tabIndex={0} aria-label={label}>{scans.map((scan) => {
    const selectable = scan.status === "complete" && (scan.scan_audio || scan.scan_video);
    return <div className={`scan-history-row${onToggleScan ? " with-selection" : ""}`} role="listitem" key={scan.id}>
      {onToggleScan && <label className="scan-review-selection"><input type="checkbox" aria-label={`Include ${scanLabel(scan)} in review`} checked={selectable && scanIsSelected(scan)} disabled={!selectable} onChange={(event) => onToggleScan(scan, event.target.checked)} /><span>Review</span></label>}
      <div><div className="scan-history-title"><strong>{scanLabel(scan)} · {scanOriginLabel(scan)} · {scan.status} {scan.edited && <span className="edited-badge">Edited</span>}</strong>{scan.status !== "scanning" && scanActionAllowed(principal, scan, "rename") && <ScanRenameAction scan={scan} onRenamed={onRenamed} />}</div>
        <p>{scanOptionsSummary(scan)}{scan.error_message && <><br /><span className="form-error">{scan.error_message}</span></>}{matchReasons[scan.id] && <><br /><span className="muted-copy">Found by {matchReasons[scan.id] === "quick_hash" ? "quick hash" : "metadata"}</span></>}<br /><time dateTime={scan.scan_started_at || undefined}>Duration {formatScanDuration(scan)}</time></p>
        <ShareAction source={{ kind: "scan", id: scan.id }} name={scanLabel(scan)} disabled={!["complete", "failed", "cancelled"].includes(scan.status)} />
      </div>
      {scanActionAllowed(principal, scan, "delete") && <button className="danger-button scan-delete-button" aria-label={`Delete ${scanLabel(scan)}`} onClick={() => onDeleteScan(scan)}>Delete</button>}
    </div>;
  })}</div>;
}

/** Confirm deletion after showing scan references and active exports.
 * @param props Scan, pending state, and confirmation callbacks.
 */
export function ScanDeleteDialog({
  scan,
  deleting,
  onClose,
  onDelete,
}: {
  scan: Scan;
  deleting: boolean;
  onClose: () => void;
  onDelete: () => void;
}) {
  const [references, setReferences] = useState<Awaited<ReturnType<typeof fetchScanReferences>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void fetchScanReferences(scan.id).then((next) => {
      if (active) setReferences(next);
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "Unable to load scan references.");
    });
    return () => { active = false; };
  }, [scan.id]);
  const activeExports = references?.active_exports || [];
  const blocked = Boolean(activeExports.length || references?.reviews.length || references?.has_hidden_references || references?.community_in_progress);
  return <ModalDialog labelledBy="delete-scan-heading" className="confirmation-dialog" onClose={deleting ? undefined : onClose}>
    <h2 id="delete-scan-heading">Delete {scanLabel(scan)}?</h2>
    {!references && !error && <p role="status">Checking scan references…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {references && blocked ? <><p>This scan cannot be deleted because of:</p><ul>{activeExports.map((format) => <li key={format}>Active {format.toUpperCase()} export</li>)}{references.reviews.map((review) => <li key={review.id}>{review.name} · modified {new Date(review.updated_at).toLocaleString()} · ID {review.id}</li>)}{references.has_hidden_references && <li>Saved reviews you cannot view</li>}{references.community_in_progress && <li>A community operation for this scan is still in progress</li>}</ul></> : references && <p>This permanently removes the scan and its findings.</p>}
    <div className="button-row"><button className="btn-secondary" onClick={onClose} disabled={deleting}>Cancel</button><button className="danger-button" onClick={onDelete} disabled={deleting || !references || blocked}>{deleting ? "Deleting…" : "Delete scan"}</button></div>
  </ModalDialog>;
}
