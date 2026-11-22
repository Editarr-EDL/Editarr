import type { VerificationCue, VerificationPreview, VerificationRequest } from "../api/api-stable-media";

function timestamp(seconds: number | null) {
  if (seconds === null) return "Pending";
  const milliseconds = Math.round(seconds * 1000);
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor(milliseconds / 60_000) % 60;
  const clock = `${String(minutes).padStart(2, "0")}:${(milliseconds % 60_000 / 1000).toFixed(3).padStart(6, "0")}`;
  return hours > 0 ? `${hours}:${clock}` : clock;
}

export type CueClip = { source: VerificationCue["source_finding_id"]; url: string };

export function VerificationCueReview({ preview, method, busy, mapped, videoToCheck, clip, viewedVideo,
  onManual, onPlay, onViewed }: {
  preview: VerificationPreview;
  method: VerificationRequest["method"];
  busy: boolean;
  mapped: number;
  videoToCheck: number;
  clip: CueClip | null;
  viewedVideo: number[];
  onManual: () => void;
  onPlay: (cue: VerificationCue) => void;
  onViewed: (id: number) => void;
}) {
  const audioFailed = method === "transcript" && mapped === 0;
  const missingTranscript = audioFailed && preview.issues.some((issue) => issue.includes("needs a cached transcript"));
  const pending = preview.cues.length - mapped;
  const timing = preview.exact ? "Exact file match" : method === "transcript" ? "Aligned using audio"
    : method === "manual" ? "From manual anchors" : "Original timestamps";

  return <section className="verification-review" aria-label="Check cues">
    {audioFailed && <div className="verification-callout verification-warning" role="alert">
      <h3>{missingTranscript ? "Target transcript needed" : "Couldn’t align confidently"}</h3>
      {preview.issues.map((issue) => <p key={issue}>{issue}</p>)}
      <button type="button" className="btn-secondary" disabled={busy} onClick={onManual}>Use manual alignment</button>
    </div>}
    {!audioFailed && <>
      <div className="verification-results-heading">
        <div><h3>Proposed cue times</h3><p className="muted-copy">{timing}</p></div>
        <p className="verification-counts" role="status"><strong>{mapped} aligned</strong>
          {pending > 0 && <span>{pending} pending</span>}</p>
      </div>
      {preview.issues.length > 0 && <ul className="verification-issues">
        {preview.issues.map((issue) => <li key={issue}>{issue}</li>)}
      </ul>}
      {preview.cues.length > 0 && <div className="scan-verification-cues" tabIndex={0} role="region" aria-label="Proposed cue times">
        <table className="library-table"><thead><tr><th scope="col">Filter</th><th scope="col">Original</th>
          <th scope="col">Proposed</th><th scope="col">Clip</th></tr></thead>
          <tbody>{preview.cues.map((cue) => <tr key={cue.source_finding_id}>
            <td>{cue.channel === "audio" ? "Audio" : cue.channel === "video" ? "Video" : "Both"}</td>
            <td>{timestamp(cue.source_start)}</td><td>{timestamp(cue.target_start)}</td>
            <td>{cue.target_start !== null && <button type="button" className="btn-secondary compact-button"
              disabled={busy} onClick={() => onPlay(cue)} aria-label={`Preview at ${timestamp(cue.target_start)}`}>
              {viewedVideo.includes(cue.source_finding_id) ? "Played" : <>Play<span> clip</span></>}
            </button>}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </>}
    {clip && <video controls tabIndex={0} preload="metadata" aria-label="Target cue preview" src={clip.url}
      onPlay={() => onViewed(clip.source)} />}
    {videoToCheck > 0 && <p className="verification-callout">{videoToCheck === 1
      ? "Play the target clip for the remaining video cue before approving."
      : `Play the target clips for the ${videoToCheck} remaining video cues before approving.`}</p>}
  </section>;
}
