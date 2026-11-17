import { useId, useState, type RefObject } from "react";
import type { MatchEvidence, RemoteFilterCandidate, StreamEvidence } from "../api/api-community-lookup";
import { ModalDialog } from "../ui/ModalDialog";
import { bandLabels, categoryLabel, resultName, strongChannel, type ResultChannel } from "./community-result-state";

type DialogProps = { candidate: RemoteFilterCandidate; channels: ResultChannel[]; target: MatchEvidence;
  onClose: () => void; returnFocusRef: RefObject<HTMLElement | null> };

const reasons: Record<string, string> = {
  audio_stream_digest_match: "Audio fingerprint matches",
  video_stream_digest_match: "Video fingerprint matches",
  audio_stream_timing_match: "Audio timing matches",
  video_stream_timing_match: "Video timing matches",
  audio_stream_playback_start: "The audio starts at a different point",
  video_stream_playback_start: "The video starts at a different point",
  audio_stream_duration: "Audio durations differ",
  video_stream_duration: "Video durations differ",
  analyzed_audio_stream: "Analyzed audio track is unknown",
  audio_track_metadata: "Audio track metadata is incomplete",
};
export const matchReason = (reason: string) => reasons[reason] ?? categoryLabel(reason);

function displaySeconds(value: string | null | undefined): string {
  if (value == null) return "Unknown";
  const [numerator, denominator = "1"] = value.split("/");
  const seconds = Number(numerator) / Number(denominator);
  if (!Number.isFinite(seconds)) return "Unknown";
  if (seconds !== 0 && Math.abs(seconds) < 0.000_001) return `${value} s`;
  const fraction = Number.isInteger(seconds) ? seconds.toFixed(3) : seconds.toFixed(6).replace(/0+$/, "");
  return `${fraction} s`;
}

function timingLabel(stream: StreamEvidence | null | undefined, field: "playback_start" | "duration") {
  const value = stream?.[field];
  return <span title={value == null ? undefined : `${value} s`}>{displaySeconds(value)}</span>;
}

function TrackComparison({ candidate, target, channels }: Pick<DialogProps, "candidate" | "target" | "channels">) {
  return <>{channels.map((channel) => {
    const result = candidate.compatibility[channel];
    const shared = candidate.evidence[`${channel}_stream`];
    const local = target[`${channel}_stream`];
    return <section key={channel} className="community-detail-section">
      <h3>{categoryLabel(channel)} match · {bandLabels[result.band]}</h3>
      <table className="community-timing-table">
        <caption className="visually-hidden">{categoryLabel(channel)} timing</caption>
        <thead><tr><th scope="col">Timing</th><th scope="col">Shared track</th><th scope="col">Your track</th></tr></thead>
        <tbody>
          <tr><th scope="row" title="Where the track begins on the media playback timeline">Playback start</th>
            <td>{timingLabel(shared, "playback_start")}</td><td>{timingLabel(local, "playback_start")}</td></tr>
          <tr><th scope="row">Duration</th><td>{timingLabel(shared, "duration")}</td><td>{timingLabel(local, "duration")}</td></tr>
        </tbody>
      </table>
      {result.reasons.length > 0 && <p>{result.reasons.map(matchReason).join(" · ")}</p>}
      {result.conflicts.length > 0 && <p className="community-needs-review">{result.conflicts.map(matchReason).join(" · ")}</p>}
      {result.missing.length > 0 && <p className="muted-copy">Missing evidence · {result.missing.map(matchReason).join(" · ")}</p>}
    </section>;
  })}</>;
}

export function CommunityFilterDetails({ candidate, channels, target, onClose, returnFocusRef }: DialogProps) {
  const id = useId();
  const { summary } = candidate;
  const counts = summary.manual_filter_counts;
  const audio = summary.analyzed_audio;
  const date = new Date(candidate.catalogCreatedAt);
  return <ModalDialog labelledBy={id} className="community-result-dialog" onClose={onClose} returnFocusRef={returnFocusRef}>
    <div className="community-dialog-heading"><div><h2 id={id}>{resultName(candidate)}</h2>
      <p className="muted-copy">{candidate.attribution.displayName} · {Number.isFinite(date.getTime()) ? date.toLocaleDateString() : "Unknown date"}</p></div>
      <button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
    <TrackComparison candidate={candidate} target={target} channels={channels} />
    <section className="community-detail-section">
      <h3>Models used</h3>
      {summary.models?.length ? <dl className="community-detail-list">{summary.models.map((model) =>
        <div key={JSON.stringify([model.capability, model.model_id, model.model_revision])}>
          <dt>{categoryLabel(model.capability)}</dt><dd>{model.model_id}{model.model_revision && ` · ${model.model_revision}`}</dd>
        </div>)}</dl> : <p className="muted-copy">No models recorded.</p>}
    </section>
    <section className="community-detail-section">
      <h3>Scan</h3>
      <dl className="community-detail-list">
        <div><dt>Filters</dt><dd>{summary.total}</dd></div>
        {counts?.edited !== 0 && <div><dt>Manually edited filters</dt><dd>{counts?.edited ?? "Not recorded"}</dd></div>}
        {counts?.added !== 0 && <div><dt>Manually added filters</dt><dd>{counts?.added ?? "Not recorded"}</dd></div>}
        {audio && <div><dt>Analyzed audio</dt><dd>{[audio.language, audio.codec, audio.description].filter(Boolean).join(" · ") || "Unknown"}</dd></div>}
        {summary.coverage.length > 0 && <div><dt>Coverage</dt><dd>{summary.coverage.map(({ channel, capability, state }) =>
          `${categoryLabel(channel)} · ${categoryLabel(capability)} (${categoryLabel(state)})`).join(", ")}</dd></div>}
        {summary.release_version && <div><dt>Editarr version</dt><dd>{summary.release_version}</dd></div>}
        {summary.scanner_behavior_version && <div><dt>Scanner version</dt><dd>{summary.scanner_behavior_version}</dd></div>}
      </dl>
    </section>
  </ModalDialog>;
}

export function CommunityFilterDownloadDialog({ candidate, channels, target, onClose, returnFocusRef, onDownload }: DialogProps & {
  onDownload: (channels: ResultChannel[], confirmed: boolean) => void;
}) {
  const id = useId();
  const [selection, setSelection] = useState(() => {
    const ready = channels.filter((channel) => strongChannel(candidate, channel));
    return ready.length ? ready : channels;
  });
  const [confirmed, setConfirmed] = useState(false);
  const needsReview = selection.filter((channel) => !strongChannel(candidate, channel));
  return <ModalDialog labelledBy={id} className="community-result-dialog" onClose={onClose} returnFocusRef={returnFocusRef}>
    <div className="community-dialog-heading"><div><h2 id={id}>Download filters</h2><p className="muted-copy">{resultName(candidate)}</p></div>
      <button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
    <fieldset className="community-download-channels">
      <legend>Channels to download</legend>
      {channels.map((channel) => <label key={channel}><input type="checkbox" checked={selection.includes(channel)}
        onChange={(event) => {
          setConfirmed(false);
          setSelection(channels.filter((key) => key === channel ? event.target.checked : selection.includes(key)));
        }} />{categoryLabel(channel)} · {strongChannel(candidate, channel) ? bandLabels[candidate.compatibility[channel].band] : "Needs review"}</label>)}
    </fieldset>
    {needsReview.length > 0 && <>
      <TrackComparison candidate={candidate} target={target} channels={needsReview} />
      <label className="community-download-confirm"><input type="checkbox" checked={confirmed}
        onChange={(event) => setConfirmed(event.target.checked)} />I reviewed the timing and approve downloading these filters with their original timestamps.</label>
    </>}
    <div className="button-row">
      <button type="button" className="btn-primary" disabled={!selection.length || Boolean(needsReview.length && !confirmed)}
        onClick={() => onDownload(selection, confirmed)}>Download filters</button>
    </div>
  </ModalDialog>;
}
