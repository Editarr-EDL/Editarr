import { useState } from "react";

import type { AudioTrackSelection } from "../api/api-audio-tracks";
import type { ScanQueueItem } from "../api/api-scan-types";
import type { VerificationRequest } from "../api/api-stable-media";

type Channels = VerificationRequest["channels"];

/** Carry timing selections through the normal New Scan setup and back to verification. */
export function useVerificationAudioScan() {
  const [setup, setSetup] = useState(false);
  const [channels, setChannels] = useState<Channels>();
  const [submitted, setSubmitted] = useState<ScanQueueItem | null>(null);
  const [requestedSelection, setRequestedSelection] = useState<AudioTrackSelection | null>(null);

  const finish = (scan?: ScanQueueItem) => {
    if (!setup) return;
    setRequestedSelection(null);
    setSetup(false);
    if (scan) setSubmitted(scan);
  };
  const reset = () => {
    finish();
    setChannels(undefined);
    setSubmitted(null);
  };
  const start = (selected: Channels, selection: AudioTrackSelection | null) => {
    setRequestedSelection(selection);
    setChannels(selected);
    setSetup(true);
  };
  return { setup, channels, submitted, requestedSelection, start, finish, reset };
}
