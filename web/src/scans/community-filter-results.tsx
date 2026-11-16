import { useEffect, useRef, useState, type ComponentProps } from "react";
import { AudioTrackField } from "./audio-track-field";
import { CommunityFilterRow } from "./community-filter-row";
import { CommunityDownloadPanel } from "./community-download-panel";
import type { useCommunityDownloads } from "./use-community-downloads";
import { bandLabels, candidateLanguage, defaultResultFilters, filterResults, requiresReview, useCommunitySelection,
  type ResultFilters, type RemoteSelection } from "./community-result-state";
import type { useCommunityLookup } from "./use-community-lookup";
import { useCan } from "../auth/auth-gate";

export type CommunityResultsProps = {
  lookup: ReturnType<typeof useCommunityLookup>;
  tracks?: ComponentProps<typeof AudioTrackField>;
  onSelectionChange?: (selections: RemoteSelection[]) => void;
  downloads?: ReturnType<typeof useCommunityDownloads>;
};
const errors: Record<string, string> = {
  not_configured: "Community service is not configured.", not_connected: "Community account is not connected.",
  network_unavailable: "Community service is offline.",
  missing_identity: "A typed media identifier is required.", unsupported_policy: "Compatibility policy is unsupported.",
  lookup_limit: "Community lookup limit reached.", membership_unavailable: "Community membership could not be verified.",
  membership_ineligible: "Community membership is required.", file_changed: "The selected file changed. Refresh the lookup.",
  audio_selection_stale: "The audio selection changed. Choose a track again.", target_changed: "The selected target changed.",
  rotation_pending: "Community key rotation is pending.", invalid_response: "Community response could not be validated.",
};

/** Public summaries only; artifact downloads and feedback are separate workflows. */
export function CommunityFilterResults({ lookup, tracks, onSelectionChange, downloads }: CommunityResultsProps) {
  const allowed = useCan("community.search");
  const [filters, setFilters] = useState<ResultFilters>(defaultResultFilters);
  const disabled = !allowed || lookup.status !== "ready" || lookup.stale || tracks?.disabled === true;
  const selection = useCommunitySelection(lookup.candidates, lookup.response?.targetKey || null, disabled);
  const selectionJson = JSON.stringify(disabled ? [] : selection.selections);
  const notifySelection = useRef(onSelectionChange);
  notifySelection.current = onSelectionChange;
  useEffect(() => { notifySelection.current?.(JSON.parse(selectionJson)); }, [selectionJson]);
  const visible = filterResults(lookup.candidates, filters);
  const setFilter = (name: keyof ResultFilters, value: string) => setFilters((previous) => ({ ...previous, [name]: value }));
  const renderGroup = (review: boolean) => {
    const candidates = visible.filter((candidate) => requiresReview(candidate) === review);
    return candidates.length ? <section aria-label={review ? "Review required results" : "Strong results"}>
      <h3>{review ? "Review Required" : "Strong Matches"}</h3>
      {candidates.map((candidate) => <CommunityFilterRow key={candidate.revisionId} candidate={candidate} disabled={disabled}
        refreshKey={JSON.stringify([lookup.key, lookup.response?.targetKey, candidate.artifactDigest])}
        selection={selection.selections.find((entry) => entry.revisionId === candidate.revisionId)}
        onChange={(channels) => selection.change(candidate, channels)} />)}
    </section> : null;
  };
  if (!allowed) return null;
  return <section className="community-filter-results" aria-label="Community filters">
    <h3>Community Filters</h3>
    {tracks && <AudioTrackField {...tracks} disabled={tracks.disabled || downloads?.running} />}
    {lookup.response && <p>Target video start: {lookup.response.targetEvidence.video_stream?.playback_start ?? "Unknown"};
      {" "}duration: {lookup.response.targetEvidence.video_stream?.duration ?? "Unknown"}.
      {" "}Target audio start: {lookup.response.targetEvidence.audio_stream?.playback_start ?? "Unknown"};
      {" "}duration: {lookup.response.targetEvidence.audio_stream?.duration ?? "Unknown"}.</p>}
    <div className="button-row">
      <button className="btn-secondary" type="button" disabled={downloads?.running || lookup.status === "loading" || tracks?.loading || tracks?.disabled} onClick={() => void lookup.refresh()}>
        {lookup.status === "idle" || lookup.status === "cancelled" ? "Find community filters" : "Refresh community filters"}</button>
      {lookup.status === "loading" && <button className="btn-secondary" type="button" onClick={lookup.cancel}>Cancel lookup</button>}
    </div>
    <p role="status" aria-live="polite">{lookup.status === "loading" ? "Looking up community filters..."
      : lookup.status === "cancelled" ? "Community lookup cancelled."
        : lookup.status === "idle" ? "Community lookup not started."
          : lookup.status === "ready" ? `${lookup.candidates.length} community results.` : "Community lookup failed."}
      {lookup.stale && " Previous results are stale."}</p>
    {lookup.error && <p role="alert">{errors[lookup.error.code] || "Community lookup is unavailable."}</p>}
    {lookup.response?.availability?.items[0].status === "ok" && <p>Available candidates: {lookup.response.availability.items[0].candidateCount}
      {lookup.response.availability.items[0].truncated ? " (lower bound)" : ""}</p>}
    {lookup.response?.warnings.length ? <p>Target evidence: {lookup.response.warnings.map((warning) => warning.replace(/_/g, " ")).join("; ")}</p> : null}
    {lookup.candidates.length > 0 && <>
      <div className="community-result-filters">
        <label>Type<select aria-label="Type" value={filters.kind} onChange={(event) => setFilter("kind", event.target.value)}>
          <option value="all">All types</option><option value="scan">AI scans</option><option value="manual">Manual filters</option><option value="derived">Derived reviews</option></select></label>
        <label>Language<select aria-label="Language" value={filters.language} onChange={(event) => setFilter("language", event.target.value)}>
          <option value="all">All languages</option>{[...new Set(lookup.candidates.map(candidateLanguage))].sort().map((language) => <option key={language} value={language}>{language === "unknown" ? "Unknown language" : language}</option>)}</select></label>
        <label>Channel<select aria-label="Channel" value={filters.channel} onChange={(event) => setFilter("channel", event.target.value)}>
          <option value="all">All channels</option><option value="video">Video</option><option value="audio">Audio</option></select></label>
        <label>Category<select aria-label="Category" value={filters.category} onChange={(event) => setFilter("category", event.target.value)}>
          <option value="all">All categories</option>{[...new Set(lookup.candidates.flatMap((candidate) => Object.keys(candidate.summary.taxonomy)))].sort().map((category) => <option key={category}>{category}</option>)}</select></label>
        <label>Coverage<select aria-label="Coverage" value={filters.coverage} onChange={(event) => setFilter("coverage", event.target.value)}>
          <option value="all">All coverage</option>{["complete", "partial", "failed", "disabled", "unsupported", "unknown"].map((state) => <option key={state}>{state}</option>)}</select></label>
        {(["video", "audio"] as const).map((channel) => <label key={channel}>{channel === "video" ? "Video band" : "Audio band"}
          <select aria-label={channel === "video" ? "Video band" : "Audio band"} value={filters[channel]} onChange={(event) => setFilter(channel, event.target.value)}>
            <option value="all">All bands</option>{Object.entries(bandLabels).map(([band, label]) => <option key={band} value={band}>{label}</option>)}</select></label>)}
        <label>Sort within bands<select aria-label="Sort within bands" value={filters.sort} onChange={(event) => setFilter("sort", event.target.value)}>
          <option value="compatibility">Catalog order</option><option value="newest">Newest</option><option value="uploader">Uploader</option>
          <option value="imports">Most imported</option><option value="quality">Revision quality</option><option value="downloads" disabled>Most downloaded unavailable</option></select></label>
      </div>
      <div className="button-row"><button className="btn-secondary" type="button" disabled={disabled} onClick={() => selection.selectStrong(visible)}>Select strong channels</button>
        <span role="status">{selection.selections.length} contributions selected
          {selection.selections.some((entry) => !visible.some((candidate) => candidate.revisionId === entry.revisionId)) && " (includes hidden results)"}</span></div>
      {renderGroup(false)}{renderGroup(true)}
      {!visible.length && <p>No results match these filters.</p>}
    </>}
    {lookup.status === "ready" && !lookup.candidates.length && <p>No community filters were found.</p>}
    {lookup.response?.page?.nextCursor && <button className="btn-secondary" type="button" disabled={disabled} onClick={() => void lookup.more()}>More community results</button>}
    {downloads && <CommunityDownloadPanel downloads={downloads} candidates={lookup.candidates} selections={selection.selections}
      targetKey={lookup.response?.targetKey || null} disabled={disabled} />}
  </section>;
}