import type { RemoteFilterCandidate } from "../api/api-community-lookup";
import { CommunityFeedback } from "./community-feedback";
import { bandLabels, resultChannels, strongChannel, type RemoteSelection, type ResultChannel } from "./community-result-state";

const reasonLabel = (reason: string) => reason.replace(/_/g, " ");
export function CommunityFilterRow({ candidate, selection, disabled, onChange, refreshKey }: {
  candidate: RemoteFilterCandidate; selection?: RemoteSelection; disabled: boolean;
  refreshKey: string;
  onChange: (channels: ResultChannel[]) => void;
}) {
  const summary = candidate.summary;
  const channels = resultChannels(candidate);
  const name = summary.name || candidate.revisionId;
  const analyzed = summary.analyzed_audio;
  const date = new Date(candidate.catalogCreatedAt);
  return <article className="community-filter-row" aria-label={name}>
    <div className="community-row-heading">
      <div><h4 title={name}>{name}</h4><p>{summary.kind === "scan" ? "AI scan" : summary.kind === "derived" ? "Derived review" : "Manual filters"}
        {" · "}{candidate.attribution.displayName}</p>
        <time dateTime={Number.isFinite(date.getTime()) ? date.toISOString() : undefined}>{Number.isFinite(date.getTime()) ? date.toLocaleDateString() : "Unknown date"}</time></div>
      <dl className="community-counts"><div><dt>Total filters</dt><dd>{summary.total}</dd></div>
        <div><dt>Video filters</dt><dd>{summary.channels.video ?? "Unknown"}</dd></div>
        <div><dt>Audio filters</dt><dd>{summary.channels.audio ?? "Unknown"}</dd></div>
        <div><dt>Unique imports</dt><dd>{candidate.statistics.imports}</dd></div>
        <div><dt>Likes</dt><dd>{candidate.statistics.likes}</dd></div>
        <div><dt>Dislikes</dt><dd>{candidate.statistics.dislikes}</dd></div></dl>
    </div>
    <p>Catalog snapshot. {candidate.ranking === null ? "Revision quality unavailable." :
      `Revision quality: ${candidate.ranking.qualityScore.toFixed(9)}; ${candidate.ranking.voteCount === 0 ? "no rating votes" : candidate.ranking.voteCount === 1 ? "one rating vote; limited evidence" : `${candidate.ranking.voteCount} rating votes`}.`}
      {" "}Quality is not a trust probability. Import volume is not trust.</p>
    <div className="community-channel-grid">
      {(["video", "audio"] as const).map((channel) => <div key={channel} className="community-channel">
        <strong>{channel === "video" ? "Video" : "Audio"}: {bandLabels[candidate.compatibility[channel].band]}</strong>
        {channels.includes(channel) ? <label><input type="checkbox" disabled={disabled}
          aria-label={`Select ${channel} from ${name}`} checked={selection?.channels.includes(channel) || false}
          onChange={(event) => onChange(event.target.checked ? [...(selection?.channels || []), channel]
            : (selection?.channels || []).filter((selected) => selected !== channel))} />
          {strongChannel(candidate, channel) ? "Strong match" : "Review required"}</label> : <span>No {channel} coverage declared</span>}
        {!!candidate.compatibility[channel].conflicts.length && <p className="community-conflict">
          {candidate.compatibility[channel].conflicts.map(reasonLabel).join("; ")}</p>}
      </div>)}
    </div>
    <p>Analyzed audio: {analyzed ? [analyzed.language || "Unknown language", analyzed.description || "Untitled", analyzed.role].join(" / ")
      : "Unknown analyzed track"}</p>
    <p>Coverage: {summary.coverage.length ? summary.coverage.map((entry) => `${entry.channel}: ${entry.capability} (${entry.state})`).join("; ") : "Unknown"}</p>
    <details><summary tabIndex={0}>Evidence and provenance</summary>
      <dl className="community-provenance">
        <dt>Name</dt><dd>{name}</dd>
        <dt>Editarr version</dt><dd>{summary.release_version || "Unknown"}</dd>
        <dt>Scanner version</dt><dd>{summary.scanner_behavior_version || "Unknown"}</dd>
        <dt>Models / settings</dt><dd>Not supplied by catalog</dd>
        <dt>Author reputation</dt><dd>Unavailable</dd>
        <dt>Source revision</dt><dd>{summary.source_revision ?? "Unknown"}</dd>
        <dt>Lineage</dt><dd>{summary.source_states?.length ? summary.source_states.map((source) => `${source.source_id}: ${source.origin} / ${source.status}`).join("; ") : "Not supplied by catalog"}</dd>
        <dt>Category counts</dt><dd>{Object.entries(summary.taxonomy).map(([category, count]) => `${category}: ${count}`).join("; ") || "None reported"}</dd>
        <dt>Artifact revision</dt><dd>{candidate.revisionId}</dd><dt>Artifact digest</dt><dd>{candidate.artifactDigest}</dd>
        <dt>Schema / registry</dt><dd>{candidate.artifactSchema} / {summary.registry_version}</dd>
      </dl>
      {(["video", "audio"] as const).map((channel) => {
        const compatibility = candidate.compatibility[channel];
        const stream = candidate.evidence[`${channel}_stream`];
        return <div key={channel}><h5>{channel === "video" ? "Video" : "Audio"} evidence</h5>
          <p>Reasons: {compatibility.reasons.map(reasonLabel).join("; ") || "None reported"}</p>
          <p>Conflicts: {compatibility.conflicts.map(reasonLabel).join("; ") || "None reported"}</p>
          <p>Missing evidence: {compatibility.missing.map(reasonLabel).join("; ") || "None reported"}</p>
          <p>Playback start: {stream?.playback_start ?? "Unknown"}; duration: {stream?.duration ?? "Unknown"}</p>
          {stream && <p>Stream provenance: {stream.provenance}; description: {stream.description || "Unknown"}</p>}
        </div>;
      })}
      {summary.coverage.map((entry, index) => <p key={index}>{entry.channel} / {entry.capability}: {entry.state};
        {" "}{entry.intervals.map((interval) => `${interval.start_seconds}-${interval.end_seconds}s`).join(", ") || "Intervals unknown"}</p>)}
    </details>
    <CommunityFeedback revisionId={candidate.revisionId} authorId={candidate.attribution.contributorId}
      disabled={disabled} refreshKey={refreshKey} />
  </article>;
}