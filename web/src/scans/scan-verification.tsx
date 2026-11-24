import { useEffect, useId, useRef, useState } from "react";

import type { AudioTrackSelection } from "../api/api-audio-tracks";
import type { Scan, ScanQueueItem } from "../api/api-scan-types";
import {
  approveScanReuse, previewScanReuse,
  type AlignmentAnchor, type VerificationCue, type VerificationPreview, type VerificationRequest,
} from "../api/api-stable-media";
import { scanLabel } from "../shared/display";
import { openScanQueue } from "../shared/scan-events";
import { ModalDialog } from "../ui/ModalDialog";
import { VerificationQueue } from "./scan-verification-queue";
import { VerificationCueReview } from "./scan-verification-review";
import { emptyAnchor, VerificationTiming } from "./scan-verification-timing";
import { useAudioTracks } from "./use-audio-tracks";
import { useVerificationTranscript } from "./use-verification-transcript";
import { useFilterClip } from "./use-filter-clip";

const steps = [
  { id: "align", label: "Align timing" }, { id: "review", label: "Check cues" }, { id: "approve", label: "Approve" },
] as const;
type Step = typeof steps[number]["id"];

interface Props {
  scan: Scan;
  originalName: string;
  target: VerificationRequest["target"];
  itemName: string;
  targetName: string;
  currentScans: Scan[];
  submittedAudioScan: ScanQueueItem | null;
  initialChannels?: VerificationRequest["channels"];
  mayStartAudioScan: boolean;
  onStartAudioScan: (channels: VerificationRequest["channels"], selection: AudioTrackSelection | null) => void;
  onClose: () => void;
  onApproved: (approved: Scan) => void;
  onApprovalError: (message: string) => void;
}

export function ScanVerificationDialog({ scan, originalName, target, itemName, targetName, currentScans, submittedAudioScan,
  initialChannels, mayStartAudioScan, onStartAudioScan, onClose, onApproved, onApprovalError }: Props) {
  const options: VerificationRequest["channels"] = [];
  if (scan.scan_audio) options.push("audio");
  if (scan.scan_video) options.push("video");
  const [channels, setChannels] = useState(initialChannels ?? options);
  const [method, setMethod] = useState<VerificationRequest["method"]>(scan.scan_audio || initialChannels ? "transcript" : "manual");
  const [step, setStep] = useState<Step>("align");
  const [showQueue, setShowQueue] = useState(!!submittedAudioScan);
  const needsAudioTrack = method === "transcript" || channels.includes("audio");
  const tracks = useAudioTracks(target, needsAudioTrack);
  const transcript = useVerificationTranscript({
    source: method === "transcript" ? scan.id : null, target, selection: tracks.selection, channels,
    scans: currentScans, submittedScan: submittedAudioScan,
  });
  const [anchors, setAnchors] = useState([emptyAnchor(), emptyAnchor()]);
  const [preview, setPreview] = useState<{ request: VerificationRequest; result: VerificationPreview } | null>(null);
  const [viewedVideo, setViewedVideo] = useState<number[]>([]);
  const [activity, setActivity] = useState<"preview" | "verify" | "approve" | null>(null);
  const clips = useFilterClip<VerificationCue["source_finding_id"]>(target);
  const busy = activity !== null || clips.loading !== null;
  const [error, setError] = useState<string | null>(null);
  const pendingRead = useRef<AbortController | null>(null);
  const dismissed = useRef(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const lastStep = useRef(step);
  const headingId = useId();
  const queueId = useId();
  const queueButton = useRef<HTMLButtonElement>(null);

  useEffect(() => () => pendingRead.current?.abort(), []);
  useEffect(() => {
    if (lastStep.current === step) return;
    lastStep.current = step;
    bodyRef.current?.focus();
  }, [step]);

  function resetPreview() {
    setError(null);
    setPreview(null);
    setViewedVideo([]);
    clips.reset();
  }

  async function prepare() {
    if (busy || !channels.length) return;
    const controller = new AbortController();
    pendingRead.current = controller;
    setActivity(transcript.status === "verify" && method === "transcript" ? "verify" : "preview");
    resetPreview();
    try {
      const provided = method === "manual" ? anchors.filter((anchor) => anchor.source.trim() || anchor.target.trim()) : [];
      const manual: AlignmentAnchor[] = provided.map((anchor) => {
        if (!anchor.source.trim() || !anchor.target.trim()) throw new Error("Enter both timestamps for every timing anchor.");
        return { source_seconds: Number(anchor.source), target_seconds: Number(anchor.target) };
      });
      if (method === "manual" && manual.length < 2) throw new Error("Enter at least two matching time pairs.");
      if (manual.some((anchor) => !Number.isFinite(anchor.source_seconds) || !Number.isFinite(anchor.target_seconds) ||
          anchor.source_seconds < 0 || anchor.target_seconds < 0)) throw new Error("Enter nonnegative timestamps in seconds.");
      const selection = needsAudioTrack ? await tracks.prepare() : null;
      const request: VerificationRequest = {
        source_scan_id: scan.id, target, channels, method,
        audio_track_index: selection?.track.stream_index,
        anchors: method === "manual" ? manual : undefined,
      };
      if (controller.signal.aborted) return;
      const result = await previewScanReuse("current", request, controller.signal);
      if (!controller.signal.aborted) {
        if (result.issues.some((issue) => issue.includes("needs a cached transcript"))) transcript.markMissing();
        setPreview({ request, result });
        setStep("review");
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to align the scan.");
    } finally {
      if (!controller.signal.aborted) setActivity(null);
    }
  }

  async function play(cue: VerificationCue) {
    if (cue.target_start === null || busy) return;
    const audioOnly = cue.channel === "audio";
    setError(null);
    await clips.play(cue.source_finding_id, {
      start_seconds: cue.target_start, end_seconds: cue.target_end,
      target_generation: preview?.result.target_generation ?? undefined,
      audio_track_index: cue.channel !== "video" ? preview?.request.audio_track_index : undefined,
      source: audioOnly ? "audio" : "visual",
      edit_action: audioOnly ? "mute" : "skip",
    });
  }

  const mapped = preview?.result.cues.filter((cue) => cue.target_start !== null).length ?? 0;
  const pending = (preview?.result.cues.length ?? 0) - mapped;
  const videoToCheck = preview?.request.method === "transcript"
    ? preview.result.cues.filter((cue) => cue.channel !== "audio" && cue.target_start !== null
        && !viewedVideo.includes(cue.source_finding_id)).length
    : 0;
  const canApprove = !!preview?.result.token && mapped > 0 && videoToCheck === 0;

  async function approve() {
    if (!preview?.result.token || !canApprove || busy) return;
    setActivity("approve");
    setError(null);
    try {
      const result = await approveScanReuse(preview.result.version_id, preview.request, preview.result.token);
      onApproved(result.scan);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Unable to approve the scan.";
      if (dismissed.current) onApprovalError(message);
      else setError(message);
    } finally { setActivity(null); }
  }

  const changeStep = (next: Step) => { setStep(next); setError(null); };
  const switchToManual = () => {
    resetPreview();
    setMethod("manual");
    setStep("align");
  };
  const closeQueue = () => { setShowQueue(false); queueButton.current?.focus(); };
  const transcriptActive = method === "transcript" && !!transcript.active;
  const transcriptPending = method === "transcript" && transcript.status === "checking";
  const transcriptMissing = method === "transcript" && transcript.status === "missing" && mayStartAudioScan;
  const approving = activity === "approve";
  const fingerprinting = approving || activity === "verify";
  const jobs = Number(!!transcript.active) + Number(fingerprinting);
  return <ModalDialog className="modal-dialog scan-verification-dialog" labelledBy={headingId}
    onClose={approving ? undefined : onClose}>
    <header className="verification-header">
      <div><p className="muted-copy">{itemName}</p><h2 id={headingId}>Verify for this version</h2></div>
      <button type="button" className="btn-secondary verification-queue-button" ref={queueButton}
        aria-controls={showQueue ? queueId : undefined} aria-expanded={showQueue}
        onClick={() => setShowQueue((visible) => !visible)}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 12h14M5 18h9m3-1.5 2.5 2.5 3.5-3.5" /></svg>
        Queue {jobs > 0 && <span className="verification-queue-count" aria-hidden="true">{jobs}</span>}
      </button>
    </header>
    {showQueue && <VerificationQueue id={queueId} source={scan.id} target={target} audio={transcript.active}
      transcriptReady={!!submittedAudioScan && transcript.status === "ready"}
      fingerprinting={fingerprinting} onClose={closeQueue} />}
    <div className="verification-versions" aria-label="Versions">
      <div><span>Original scan</span><strong>{scanLabel(scan)}{originalName ? ` · ${originalName}` : ""}</strong>
        <small>{scan.finding_count} {scan.finding_count === 1 ? "cue" : "cues"} in source scan</small></div>
      <span className="verification-direction" aria-hidden="true">→</span>
      <div><span>Target version</span><strong>{targetName}</strong></div>
    </div>
    <nav className="verification-steps" aria-label="Verification steps">
      <ol>{steps.map(({ id, label }, index) => <li key={id}>
        <button type="button" aria-current={step === id ? "step" : undefined}
          disabled={busy || (id === "review" && !preview) || (id === "approve" && !canApprove)}
          onClick={() => changeStep(id)}><span aria-hidden="true">{index + 1}</span> {label}</button>
      </li>)}</ol>
    </nav>
    <div className="verification-body" tabIndex={-1} ref={bodyRef} role="group"
      aria-label={steps.find(({ id }) => id === step)?.label}>
      {step === "align" && <VerificationTiming options={options} channels={channels} method={method} anchors={anchors}
        tracks={tracks} busy={busy} transcript={{ ...transcript, active: transcriptActive }} mayStartAudioScan={mayStartAudioScan}
        onChannels={(updated) => { setChannels(updated); resetPreview(); }}
        onMethod={(updated) => { setMethod(updated); resetPreview(); }}
        onAnchors={(updated) => { setAnchors(updated); resetPreview(); }}
        onTrack={(selection) => { resetPreview(); tracks.onChange(selection); }} />}
      {step === "review" && preview && <VerificationCueReview preview={preview.result} method={preview.request.method}
        busy={busy} mapped={mapped} videoToCheck={videoToCheck} clip={clips.clip} viewedVideo={viewedVideo}
        onManual={switchToManual} onPlay={(cue) => void play(cue)}
        onViewed={(id) => setViewedVideo((viewed) => viewed.includes(id) ? viewed : [...viewed, id])} />}
      {step === "approve" && <section className="verification-approval" aria-label="Approve">
        <h3>Save cues for this version</h3>
        <p>{mapped} aligned {mapped === 1 ? "cue will" : "cues will"} be available in Review.
          {pending > 0 && ` ${pending} ${pending === 1 ? "cue stays" : "cues stay"} pending.`}</p>
        <p className="muted-copy">Editarr checks the target file before saving. For large files, it reads sampled blocks. Follow the check in Queue.</p>
      </section>}
      {(error || clips.error) && <p className="form-error" role="alert">{error ?? clips.error}</p>}
      {(activity || clips.loading !== null) && <p className="form-status" role="status">{approving
        ? "Checking the target file’s fingerprint before saving…"
        : activity === "verify" ? "Checking the file before reusing the saved transcript…"
          : clips.loading !== null ? "Loading the cue preview…" : "Calculating proposed cue times…"}</p>}
    </div>
    <footer className="verification-footer">
      <div className="verification-footer-back">
        {!approving && <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>}
        {!approving && step !== "align" && <button type="button" className="btn-secondary" disabled={busy}
          onClick={() => changeStep(step === "review" ? "align" : "review")}>{step === "review" ? "Back to timing" : "Back to cues"}</button>}
      </div>
      {step === "align" && (transcriptActive
        ? <button type="button" className="btn-primary" onClick={() => setShowQueue(true)}>View audio scan in Queue</button>
        : <button type="button" className="btn-primary" disabled={busy || !channels.length || (needsAudioTrack && !tracks.ready) || transcriptPending}
            onClick={() => transcriptMissing ? onStartAudioScan(channels, tracks.selection) : void prepare()}>
            {transcriptMissing ? "Run audio scan" : "Preview alignment"}
          </button>)}
      {step === "review" && mapped > 0 && <button type="button" className="btn-primary" disabled={busy || !canApprove}
        onClick={() => changeStep("approve")}>Continue to approval</button>}
      {step === "approve" && (approving
        ? <button type="button" className="btn-primary" onClick={() => {
            dismissed.current = true;
            onClose();
            openScanQueue();
          }}>Continue in Queue</button>
        : <button type="button" className="btn-primary" disabled={busy || !canApprove}
            onClick={() => void approve()}>Approve for this version</button>)}
    </footer>
  </ModalDialog>;
}
