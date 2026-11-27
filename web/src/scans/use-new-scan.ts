import { useEffect, useState } from "react";
import { fetchCapabilities } from "../api/api-service";
import { enqueueScan, requiresAudioTrackRefresh } from "../api/api-scans";
import { startCreditsDetection } from "../api/api-reviews";
import type { ScanCapabilities } from "../api/api-system-types";
import type { ScanOptions } from "../api/api-scan-types";
import { validateDisplayName } from "../shared/name-validation";
import { resolveModelChoice } from "./model-options";
import { useAudioTracks } from "./use-audio-tracks";
import type { useVerificationAudioScan } from "./use-verification-audio-scan";

export type NewScanType = "audio" | "video" | "combined" | "credits";
type SubmitError = { message: string; refreshTracks?: boolean };

/**
 * Own the New Scan form for one catalog item: scan type, model choices with capability defaults,
 * video settings, the audio-track inventory, and queue submission.
 * @param options.mediaSourceId Version to scan; owned by the caller because it follows the page's version.
 * @param options.onMessage Page status line; cleared on submit and set when a scan is queued.
 * @param options.onQueued Refresh the caller's scan list after a successful submission.
 */
export function useNewScan({ itemId, mediaSourceId, mayScan, verificationAudio, onMessage, onQueued }: {
  itemId: string;
  mediaSourceId: string | null;
  mayScan: boolean;
  verificationAudio: ReturnType<typeof useVerificationAudioScan>;
  onMessage: (message: string | null) => void;
  onQueued: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [scanType, setScanTypeState] = useState<NewScanType>("audio");
  const scanAudio = scanType === "audio" || scanType === "combined";
  const scanVideo = scanType === "video" || scanType === "combined";
  const [audioModel, setAudioModel] = useState("whisper");
  const [audioFilter, setAudioFilter] = useState("better_profanity");
  const [videoModel, setVideoModel] = useState("");
  const [coarseScanInterval, setCoarseScanInterval] = useState(5);
  const [coarseScanEnabled, setCoarseScanEnabled] = useState(false);
  const [qwenModel, setQwenModel] = useState("");
  const [qwenSummariesPerSegment, setQwenSummariesPerSegment] = useState(4);
  const [qwenSegmentJoinGapSeconds, setQwenSegmentJoinGapSeconds] = useState(3);
  const [capabilities, setCapabilities] = useState<ScanCapabilities | null>(null);
  const [adding, setAdding] = useState(false);
  const [submitError, setSubmitError] = useState<SubmitError | null>(null);
  const audioTracks = useAudioTracks({ item_id: itemId, media_source_id: mediaSourceId },
    mayScan && open && scanAudio, verificationAudio.requestedSelection);
  const needsAudioTrackRefresh = !!(scanAudio && submitError?.refreshTracks);
  const videoSettingsInvalid = scanVideo && (coarseScanEnabled && coarseScanInterval <= 0
    || qwenSummariesPerSegment < 1 || qwenSegmentJoinGapSeconds < 0);

  useEffect(() => {
    if (!mayScan || !open) return;
    void fetchCapabilities().then((next) => {
      setCapabilities(next);
      setAudioModel((current) => (
        next.audio_models.length > 0 && !next.audio_models.some((model) => model.id === current)
          ? next.audio_models[0].id
          : current
      ));
      setAudioFilter((current) => (
        next.audio_filters.length > 0
        && !next.audio_filters.some((filter) => filter.id === current)
          ? next.audio_filters[0].id
          : current
      ));
      setVideoModel((current) => resolveModelChoice(current, next.video_models));
      setQwenModel((current) => resolveModelChoice(current, next.qwen_models));
    }).catch(() => onMessage("Unable to load scan options."));
    // onMessage is the caller's stable state setter; reload only when the form opens.
  }, [mayScan, open]);

  /** Open the form; `audio` preselects an audio scan for the verification timing flow. */
  const openForm = (audio = false) => {
    if (!mayScan) return;
    if (audio) setScanTypeState("audio");
    setSubmitError(null);
    setOpen(true);
  };
  const close = () => { setOpen(false); verificationAudio.finish(); };
  const setScanType = (next: NewScanType) => { setSubmitError(null); setScanTypeState(next); };
  const refreshAudioTracks = () => {
    setSubmitError((current) => (current?.refreshTracks ? null : current));
    audioTracks.onRetry();
  };

  /** Queue the configured scan. `rawName` is the optional scan name typed in the form. */
  const submit = async (rawName: string) => {
    if (!mayScan || adding || (scanAudio && !audioTracks.ready) || needsAudioTrackRefresh) return;
    const scanName = validateDisplayName(rawName, false);
    if (scanName.error) { setSubmitError({ message: scanName.error }); return; }
    setAdding(true); setSubmitError(null); onMessage(null);
    if (scanType === "credits") {
      try {
        const result = await startCreditsDetection({
          target: { source: "jellyfin", item_id: itemId, media_source_id: mediaSourceId },
          qwen_model: null,
        });
        setOpen(false);
        onMessage(`Credits scan ${result.scan.scan_id} added to the queue.`);
        await onQueued();
      } catch (error) {
        setSubmitError({ message: error instanceof Error ? error.message : "Unable to add credits scan to queue." });
      } finally {
        setAdding(false);
      }
      return;
    }
    const options: ScanOptions = {
      name: scanName.normalized,
      scan_audio: scanAudio,
      audio_model: scanAudio ? audioModel : null,
      audio_filter: scanAudio ? audioFilter : null,
      audio_selection: scanAudio ? audioTracks.selection : undefined,
      scan_video: scanVideo,
      ...(scanVideo ? (videoModel ? { video_model: videoModel } : {}) : { video_model: null }),
      coarse_scan_interval: scanVideo && coarseScanEnabled ? coarseScanInterval : undefined,
      coarse_scan_enabled: scanVideo && coarseScanEnabled,
      ...(qwenModel ? { qwen_model: qwenModel } : {}),
      qwen_summaries_per_segment: qwenSummariesPerSegment,
      qwen_segment_join_gap_seconds: qwenSegmentJoinGapSeconds,
    };
    try {
      const scan = await enqueueScan(itemId, options, mediaSourceId);
      verificationAudio.finish(scan);
      setOpen(false);
      onMessage(`Scan ${scan.scan_id} added to the queue.`);
      await onQueued();
    } catch (error) {
      setSubmitError({ message: error instanceof Error ? error.message : "Unable to add scan to queue.",
        refreshTracks: requiresAudioTrackRefresh(error) });
    } finally { setAdding(false); }
  };

  return {
    open, openForm, close, submit, capabilities, adding, submitError, clearError: () => setSubmitError(null),
    scanType, setScanType, scanAudio, scanVideo, audioTracks, needsAudioTrackRefresh, refreshAudioTracks,
    audioModel, setAudioModel, audioFilter, setAudioFilter, videoModel, setVideoModel, qwenModel, setQwenModel,
    video: {
      coarseScanEnabled, setCoarseScanEnabled, coarseScanInterval, setCoarseScanInterval,
      qwenSummariesPerSegment, setQwenSummariesPerSegment, qwenSegmentJoinGapSeconds, setQwenSegmentJoinGapSeconds,
      invalid: videoSettingsInvalid,
    },
    canSubmit: !adding && !needsAudioTrackRefresh && !(scanAudio && !audioTracks.ready) && !videoSettingsInvalid,
    setupLocked: adding || verificationAudio.setup,
  };
}

export type NewScanForm = ReturnType<typeof useNewScan>;
