import { useEffect, useState } from "react";

import type { AudioTrackSelection } from "../api/api-audio-tracks";
import { fetchVerificationTranscriptStatus, type VerificationRequest } from "../api/api-stable-media";
import type { Scan, ScanQueueItem } from "../api/api-scan-types";

export type TranscriptStatus = "checking" | "ready" | "verify" | "missing" | "unknown";
const waitingStatuses = new Set(["queued", "scanning", "pausing", "paused", "cancelling", "acquiring_model", "orphaned"]);

/** Scope queued audio work and readiness to the selected physical version and stream. */
export function useVerificationTranscript({ source, target, selection, channels, scans, submittedScan }: {
  source: string | null;
  target: VerificationRequest["target"];
  selection: AudioTrackSelection | null;
  channels: VerificationRequest["channels"];
  scans: Scan[];
  submittedScan: ScanQueueItem | null;
}) {
  const trackIndex = selection?.track.stream_index;
  const { item_id: itemId, media_source_id: versionId } = target;
  const matching = (scan: Scan) => {
    const audio = scan.analyzed_audio_selection || scan.requested_audio_selection;
    return trackIndex !== undefined && scan.scan_audio && scan.origin === "generated" &&
      scan.jellyfin_item_id === itemId && scan.media_source_id === versionId &&
      audio?.track.stream_index === trackIndex && audio.file_version === selection?.file_version;
  };
  const submitted = submittedScan && (scans.find((scan) => scan.id === submittedScan.id) ?? submittedScan);
  const queuedForTrack = (scan: Scan): scan is ScanQueueItem =>
    "queue_id" in scan && waitingStatuses.has(scan.status) && matching(scan);
  const active = submitted && queuedForTrack(submitted) ? submitted : scans.find(queuedForTrack);
  const completed = scans.filter((scan) => scan.status === "complete" && matching(scan)).map((scan) => scan.id).join(",");
  const selectedChannels = channels.join(",");
  const key = source && selection && selectedChannels
    ? JSON.stringify([source, itemId, versionId, selection.file_version, trackIndex, selectedChannels, active?.id, completed])
    : null;
  const [state, setState] = useState<{ key: string; status: TranscriptStatus } | null>(null);
  const waitingForScan = !!active;

  useEffect(() => {
    if (!key || !source || trackIndex === undefined || waitingForScan) return;
    const controller = new AbortController();
    void fetchVerificationTranscriptStatus({
      source_scan_id: source,
      target: { source: "jellyfin", item_id: itemId, media_source_id: versionId },
      audio_track_index: trackIndex,
      channels: selectedChannels.split(",") as VerificationRequest["channels"],
      method: "transcript",
    }, controller.signal).then(({ ready, verify_existing }) => {
      const status = ready === true ? "ready" : verify_existing ? "verify" : ready === false ? "missing" : "unknown";
      if (!controller.signal.aborted) setState({ key, status });
    }).catch(() => {
      if (!controller.signal.aborted) setState({ key, status: "unknown" });
    });
    return () => controller.abort();
  }, [key, source, trackIndex, itemId, versionId, selectedChannels, waitingForScan]);

  const status = !key ? "unknown" : state?.key === key ? state.status : "checking";
  const currentFailure = submitted && matching(submitted) && ["failed", "error", "cancelled"].includes(submitted.status)
    ? submitted.error_message || "The audio scan did not complete. You can retry it."
    : null;
  const markMissing = () => { if (key) setState({ key, status: "missing" }); };
  return { status, active, failure: currentFailure, markMissing };
}
