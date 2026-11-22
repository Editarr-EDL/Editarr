import type { VerificationRequest } from "../api/api-stable-media";
import { AudioTrackField } from "./audio-track-field";
import type { useAudioTracks } from "./use-audio-tracks";
import type { TranscriptStatus } from "./use-verification-transcript";

export type AnchorInput = { source: string; target: string };
export const emptyAnchor = (): AnchorInput => ({ source: "", target: "" });

type Channel = VerificationRequest["channels"][number];

export function VerificationTiming({ options, channels, method, anchors, tracks, busy, transcript,
  mayStartAudioScan, onChannels, onMethod, onAnchors, onTrack }: {
  options: Channel[];
  channels: Channel[];
  method: VerificationRequest["method"];
  anchors: AnchorInput[];
  tracks: ReturnType<typeof useAudioTracks>;
  busy: boolean;
  transcript: { active: boolean; status: TranscriptStatus; failure: string | null };
  mayStartAudioScan: boolean;
  onChannels: (channels: Channel[]) => void;
  onMethod: (method: VerificationRequest["method"]) => void;
  onAnchors: (anchors: AnchorInput[]) => void;
  onTrack: ReturnType<typeof useAudioTracks>["onChange"];
}) {
  const useAudio = method === "transcript" || channels.includes("audio");
  const updateAnchor = (index: number, field: keyof AnchorInput, value: string) => {
    onAnchors(anchors.map((anchor, position) => position === index ? { ...anchor, [field]: value } : anchor));
  };
  const transcriptNotice = transcript.active
    ? ["Audio scan in Queue", "Return to preview alignment when the selected audio track has a transcript."]
    : transcript.status === "verify" ? ["Existing audio scan found", "Preview alignment checks that the file is unchanged so Editarr can reuse the saved transcript. Follow the check in Queue."]
      : transcript.status === "ready" ? ["Target transcript ready", "Editarr can match saved dialogue or timing patterns from flagged audio cues."]
      : transcript.status === "checking" ? ["Checking target transcript…", "Looking for timed dialogue on the selected audio track."]
        : transcript.status === "missing" ? ["Target transcript needed", mayStartAudioScan
          ? "Run an audio scan on this version so Editarr can try matching the saved cues."
          : "Choose manual anchors if an audio transcript is unavailable."]
          : ["Align using the target audio", "Preview alignment checks the selected track for timed dialogue."];

  return <section className="verification-timing" aria-label="Align timing">
    <fieldset className="verification-channels" disabled={busy}>
      <legend>Filters to verify</legend>
      {options.map((channel) => <label key={channel}>
        <input type="checkbox" checked={channels.includes(channel)} onChange={(event) =>
          onChannels(event.target.checked ? [...channels, channel] : channels.filter((value) => value !== channel))} />
        {channel === "audio" ? "Audio" : "Video"}
      </label>)}
    </fieldset>
    <div className="verification-fields">
      <label className="field"><span>Timing method</span>
        <select value={method} disabled={busy} onChange={(event) => onMethod(event.target.value as VerificationRequest["method"])}>
          <option value="transcript">Align using audio</option>
          <option value="original">Keep original timing</option>
          <option value="manual">Manual timing anchors</option>
        </select>
      </label>
      {useAudio && <AudioTrackField {...tracks} disabled={busy} onChange={onTrack}
        label="Target audio track" showSelectedLabel={false} />}
    </div>
    {method === "transcript" && tracks.ready && <div className="verification-callout" role="status">
      <h3>{transcriptNotice[0]}</h3><p>{transcriptNotice[1]}</p>
    </div>}
    {method === "transcript" && transcript.failure && <p className="form-error" role="alert">{transcript.failure}</p>}
    {method === "manual" && <fieldset className="verification-anchors" disabled={busy}>
      <legend>Match timing points</legend>
      <p className="muted-copy">Enter at least two matching times in seconds on the original and target.</p>
      {anchors.map((anchor, index) => <div className="verification-fields" key={index}>
        <label className="field"><span>Original time {index + 1}</span>
          <input type="number" min="0" step="any" value={anchor.source}
            onChange={(event) => updateAnchor(index, "source", event.target.value)} />
        </label>
        <label className="field"><span>Target time {index + 1}</span>
          <input type="number" min="0" step="any" value={anchor.target}
            onChange={(event) => updateAnchor(index, "target", event.target.value)} />
        </label>
      </div>)}
      <button type="button" className="btn-secondary compact-button" onClick={() => onAnchors([...anchors, emptyAnchor()])}>
        Add anchor
      </button>
    </fieldset>}
  </section>;
}
