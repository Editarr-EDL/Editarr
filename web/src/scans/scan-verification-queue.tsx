import { useState } from "react";

import { fetchFingerprintQueue, type FingerprintQueueItem, type VerificationRequest } from "../api/api-stable-media";
import type { ScanQueueItem } from "../api/api-scan-types";
import { ACTIVE_SCAN_POLL_MS, IDLE_SCAN_POLL_MS, useScanPolling } from "./use-scan-polling";

/** Show real progress for work on this target, while the verification dialog stays open. */
export function VerificationQueue({ id, source, target, audio, transcriptReady, approving, onClose }: {
  id: string;
  source: string;
  target: VerificationRequest["target"];
  audio?: ScanQueueItem;
  transcriptReady: boolean;
  approving: boolean;
  onClose: () => void;
}) {
  const [fingerprints, setFingerprints] = useState<FingerprintQueueItem[]>([]);
  const [fingerprintError, setFingerprintError] = useState<string | null>(null);
  useScanPolling(async () => {
    if (!approving) return IDLE_SCAN_POLL_MS;
    try {
      setFingerprints(await fetchFingerprintQueue());
      setFingerprintError(null);
    } catch (reason) {
      setFingerprintError(reason instanceof Error ? reason.message : "Unable to load fingerprint progress.");
    }
    return ACTIVE_SCAN_POLL_MS;
  }, approving);
  const fingerprint = approving && fingerprints.find((item) => item.source_scan_id === source &&
    item.item_id === target.item_id && item.media_source_id === target.media_source_id);
  const percent = fingerprint && fingerprint.bytes_total > 0
    ? Math.min(100, Math.floor(100 * fingerprint.bytes_read / fingerprint.bytes_total)) : undefined;
  const audioLabel = audio?.status === "orphaned" ? "Audio scan needs attention in the main Queue"
    : audio?.status === "scanning" ? "Scanning target audio" : "Audio scan in Queue";
  return <section className="verification-queue" id={id} aria-label="Verification Queue">
    <header><h3>Queue</h3><button type="button" className="icon-button" aria-label="Close Queue" onClick={onClose}>×</button></header>
    {audio && <div className="verification-queue-job">
      <strong>{audioLabel}</strong>
      <p>{audio.progress_message || audio.progress_stage || audio.status}</p>
      <progress aria-label="Target audio scan progress" max={100} value={audio.progress_percent ?? undefined} />
    </div>}
    {approving && <div className="verification-queue-job">
      <strong>{fingerprint && fingerprint.reading ? "Calculating file fingerprint" : "Verifying target file"}</strong>
      {percent !== undefined && <p>{percent}% read</p>}
      {fingerprintError && <p className="muted-copy">{fingerprintError}</p>}
      <progress aria-label="File fingerprint progress" max={100} value={percent} />
    </div>}
    {!audio && !approving && <p className="muted-copy" role="status">{transcriptReady
      ? "Target transcript ready. You can preview alignment."
      : "No verification work in Queue."}</p>}
  </section>;
}
