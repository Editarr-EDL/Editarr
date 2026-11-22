import { useEffect, useRef, useState } from "react";

import type { ScanQueueItem } from "../api/api-scan-types";
import type { VerificationRequest } from "../api/api-stable-media";
import type { useAudioTracks } from "./use-audio-tracks";

type Channels = VerificationRequest["channels"];

/** Carry timing selections through the normal New Scan setup and back to verification. */
export function useVerificationAudioScan(tracks: ReturnType<typeof useAudioTracks>) {
  const [setup, setSetup] = useState(false);
  const [channels, setChannels] = useState<Channels>();
  const [submitted, setSubmitted] = useState<ScanQueueItem | null>(null);
  const requestedTrack = useRef<number | undefined>(undefined);
  useEffect(() => {
    const index = requestedTrack.current;
    if (!setup || !tracks.inventory || index === undefined) return;
    requestedTrack.current = undefined;
    const track = tracks.inventory.tracks.find((candidate) => candidate.stream_index === index);
    if (track && tracks.selection?.track.stream_index !== index) {
      tracks.onChange({ file_version: tracks.inventory.file_version, track });
    }
  }, [setup, tracks.inventory]);

  const finish = (scan?: ScanQueueItem) => {
    if (!setup) return;
    requestedTrack.current = undefined;
    setSetup(false);
    if (scan) setSubmitted(scan);
  };
  const reset = () => {
    finish();
    setChannels(undefined);
    setSubmitted(null);
  };
  const start = (selected: Channels, trackIndex: number | undefined) => {
    requestedTrack.current = trackIndex;
    setChannels(selected);
    setSetup(true);
  };
  return { setup, channels, submitted, start, finish, reset };
}
