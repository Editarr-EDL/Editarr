import type { SharingPreview } from "../api/api-sharing";
import { formatTime } from "../shared/display";

export function SharingPreviewDetails({ value }: { value: SharingPreview }) {
  const { artifact, manifest, exclusions, warnings } = value.preview;
  return <section className="sharing-preview" aria-label="Outgoing contribution">
    <h3>{artifact.name}</h3>
    <dl className="sharing-facts">
      <dt>Evidence</dt><dd>{artifact.kind} · revision {artifact.source_revision}</dd>
      <dt>Acting contributor</dt><dd>{artifact.author.contributor_id}</dd>
      <dt>Filters</dt><dd>{manifest.summary.total}</dd>
      <dt>Coverage</dt><dd>{artifact.coverage.map((item) => `${item.channel}: ${item.state}`).join(" / ") || "Unknown"}</dd>
      <dt>Analyzed audio</dt><dd>{artifact.analyzed_audio
        ? `${artifact.analyzed_audio.language || "Unknown language"} / ${artifact.analyzed_audio.description || "Untitled"}`
        : "Unknown analyzed track / Review required"}</dd>
    </dl>
    {artifact.audio_association === "unknown" && <p role="status">Audio track unknown. Review required.</p>}
    {exclusions.length > 0 && <div><h4>Excluded filters</h4><ul>{exclusions.map((item) => <li key={item.event_id}>
      {item.event_id} · {item.channel || "filter"} · {item.reason.replace(/_/g, " ")}
    </li>)}</ul></div>}
    {warnings.length > 0 && <ul aria-label="Preview warnings">{warnings.map((warning) => <li key={warning}>{warning.replace(/_/g, " ")}</li>)}</ul>}
    <div className="sharing-table-scroll" role="region" aria-label="Outgoing filters" tabIndex={0}>
      <table className="library-table"><thead><tr><th>Time</th><th>Channel</th><th>Classification</th><th>Public note</th></tr></thead>
        <tbody>{artifact.findings.map((finding) => <tr key={finding.event_id}>
          <td>{formatTime(finding.start_seconds)}–{formatTime(finding.end_seconds)}</td><td>{finding.channel}</td>
          <td>{finding.category || "Unclassified"} / {finding.subcategory || "Unknown"}</td><td>{finding.note || ""}</td>
        </tr>)}</tbody></table>
      {!artifact.findings.length && <p>No filters.</p>}
    </div>
    <details><summary>Exact outgoing metadata and lineage</summary><pre role="region" aria-label="Exact outgoing metadata and lineage" tabIndex={0}>{JSON.stringify(value.preview, null, 2)}</pre></details>
    <p className="muted-copy sharing-digest">SHA-256 {value.preview.artifact_digest}</p>
  </section>;
}