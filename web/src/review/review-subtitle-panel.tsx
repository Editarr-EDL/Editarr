import type { SubtitleSource } from "../api/api-review-subtitles";
import { formatTime } from "../shared/display";
import type { ReviewSubtitles } from "./use-review-subtitles";

export function subtitleSourceDetails(source: SubtitleSource) {
  return [
    source.format.toUpperCase(),
    { external: "External file", embedded: "Embedded track", transcript: "Audio transcript" }[source.kind],
    source.forced && "Forced",
    source.sdh && "SDH",
  ].filter(Boolean).join(" · ");
}

export function SubtitlePlaybackNotice({ forced }: { forced: boolean }) {
  return <p className="muted-copy">
    Everyone who can play this version in Jellyfin can see this track.
    {forced && " Jellyfin may automatically select this forced track for other viewers."}
  </p>;
}

export function ReviewSubtitlePanel({ subtitles, onContinue, onStartAudioScan }: {
  subtitles: ReviewSubtitles;
  onContinue: () => void;
  onStartAudioScan?: () => void;
}) {
  const { sources, source, preview, previewing, publicationBlockReason } = subtitles;
  if (subtitles.inventoryError) return <section className="workflow-release-panel" aria-label="Filtered subtitles">
    <p className="load-error" role="alert">{subtitles.inventoryError}</p>
    <button type="button" className="btn-secondary" onClick={subtitles.reload}>Retry</button>
  </section>;
  if (!sources) return <p className="muted-copy" role="status">Finding subtitle tracks…</p>;
  const trackTitle = preview?.kind === "ready" ? preview.value.track_title : sources.track_title;
  const canContinue = preview?.kind === "ready" && !publicationBlockReason;
  const previewLabel = previewing ? "Preparing preview…"
    : preview?.kind === "ready" ? "Retry cue preview" : "Preview cue changes";

  return <div className="workflow-release-grid">
    <section className="workflow-release-panel" aria-labelledby="subtitle-source-heading">
      <h3 id="subtitle-source-heading">Subtitle source</h3>
      {sources.audio_sources.length > 1 && <label className="field">
        <span>Reviewed audio</span>
        <select value={subtitles.audioId ?? ""} onChange={(event) => subtitles.chooseAudio(event.target.value)}>
          <option value="">Choose a reviewed audio track</option>
          {sources.audio_sources.map((audio) => <option key={audio.id} value={audio.id}>{audio.label}</option>)}
        </select>
      </label>}
      {sources.fallback === "available" && <p className="muted-copy">Use the cached audio transcript to generate SRT.</p>}
      {sources.fallback === "choose_audio" && <p className="muted-copy">Choose a reviewed audio track to check for a transcript.</p>}
      {sources.fallback === "scan_required" && <>
        <p className="muted-copy">No suitable subtitle track or cached transcript is available. Run an audio scan and include it in this review to use its transcript.</p>
        {onStartAudioScan && <div><button type="button" className="btn-secondary" onClick={onStartAudioScan}>Set up audio scan</button></div>}
      </>}
      {sources.sources.length > 0 && <div className="workflow-release-options" role="group" aria-label="Subtitle tracks">
        {sources.sources.map((candidate) => <button type="button" className="btn-secondary workflow-release-option"
          key={candidate.id} disabled={!candidate.supported} aria-pressed={candidate.id === source?.id}
          onClick={() => subtitles.chooseSource(candidate)}>
          <span><strong>{candidate.label}</strong><small>{subtitleSourceDetails(candidate)}</small></span>
          {candidate.recommended ? <span className="workflow-subtitle-tag">Suggested</span>
            : !candidate.supported ? <span className="workflow-subtitle-tag">Unsupported</span> : null}
        </button>)}
      </div>}
    </section>
    <section className="workflow-release-panel" aria-labelledby="subtitle-preview-heading">
      <h3 id="subtitle-preview-heading">Cue preview</h3>
      {publicationBlockReason && <p className="load-error" role="alert">{publicationBlockReason}</p>}
      {source && trackTitle && <p className="workflow-subtitle-title">Jellyfin track title <strong>{trackTitle}</strong></p>}
      {source && !publicationBlockReason && <SubtitlePlaybackNotice forced={source.forced} />}
      {!source && <p className="muted-copy">Cue previews appear when you choose a subtitle source.</p>}
      {source && !preview && <p className="muted-copy">Preview how this saved review changes the selected track.</p>}
      {preview?.kind === "error" && <p className="load-error" role="alert">{preview.message}</p>}
      {preview?.kind === "ready" && <>
        <p className="muted-copy">{preview.value.changed_cues} changed · {preview.value.unchanged_cues} unchanged</p>
        {preview.value.cues.length > 0 && <table className="workflow-subtitle-cues">
          <thead><tr><th scope="col">Time</th><th scope="col">Original</th><th scope="col">Filtered</th></tr></thead>
          <tbody>{preview.value.cues.map((cue, index) => <tr key={`${cue.start_seconds}-${cue.end_seconds}-${index}`}>
            <td>{formatTime(cue.start_seconds)}</td><td>{cue.original}</td>
            <td>{cue.filtered || <i>Cue removed</i>}</td>
          </tr>)}</tbody>
        </table>}
      </>}
      {source && <div className="button-row">
        {canContinue
          ? <button type="button" className="btn-primary" onClick={onContinue}>Continue to Publish</button>
          : <button type="button" className="btn-primary" disabled={previewing} onClick={() => void subtitles.requestPreview()}>{previewLabel}</button>}
      </div>}
    </section>
  </div>;
}
