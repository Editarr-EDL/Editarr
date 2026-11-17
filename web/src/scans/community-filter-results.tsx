import { useEffect, useRef, useState, type ComponentProps, type MouseEvent } from "react";
import type { MatchBand } from "../api/api-community-lookup";
import { useCan } from "../auth/auth-gate";
import { AudioTrackField } from "./audio-track-field";
import { CommunityDownloadPanel } from "./community-download-panel";
import { CommunityFilterDetails, CommunityFilterDownloadDialog } from "./community-filter-details";
import { CommunityFilterRow } from "./community-filter-row";
import { CommunityIcon } from "./community-icon";
import { CommunityMatchFilter } from "./community-match-filter";
import { communityRows, compatibleChannels, matchBandCounts, matchBands, selectDownload, strongChannel,
  type ChannelFilter, type ResultChannel, type ResultSort } from "./community-result-state";
import type { useCommunityDownloads } from "./use-community-downloads";
import type { useCommunityLookup } from "./use-community-lookup";

export type CommunityResultsProps = {
  lookup: ReturnType<typeof useCommunityLookup>;
  tracks?: ComponentProps<typeof AudioTrackField>;
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
const columns = [{ id: "name", label: "Scan" }, { id: "match", label: "Match" }, { id: "filters", label: "Filters" }] as const;
type Popup = { kind: "details" | "download"; key: string; revisionId: string; digest: string };

export function CommunityFilterResults({ lookup, tracks, downloads }: CommunityResultsProps) {
  const allowed = useCan("community.search");
  const [bands, setBands] = useState<ReadonlySet<MatchBand>>(() => new Set(matchBands));
  const [channel, setChannel] = useState<ChannelFilter>("all");
  const [sort, setSort] = useState<ResultSort>({ column: "match", direction: "ascending" });
  const [popup, setPopup] = useState<Popup | null>(null);
  const returnFocus = useRef<HTMLElement>(null);
  const targetKey = lookup.response?.targetKey ?? null;
  const target = lookup.response?.targetEvidence;
  const ready = lookup.status === "ready" && !lookup.stale && !tracks?.disabled;
  const canDownload = !!downloads?.allowed && ready && !!targetKey && !downloads.running && !downloads.checking;
  const visible = communityRows(lookup.candidates, channel, bands, sort);
  const popupKey = JSON.stringify([lookup.key, targetKey, channel, matchBands.filter((band) => bands.has(band))]);
  const popupRow = popup?.key === popupKey
    ? visible.find(({ candidate }) => candidate.revisionId === popup.revisionId && candidate.artifactDigest === popup.digest)
    : undefined;
  const close = () => setPopup(null);
  useEffect(() => {
    if (popup && (!popupRow || popup.kind === "download" && !canDownload)) setPopup(null);
  }, [popup, popupRow, canDownload]);

  if (!allowed) return null;
  const open = (event: MouseEvent<HTMLButtonElement>, row: typeof visible[number], kind: Popup["kind"]) => {
    returnFocus.current = event.currentTarget;
    setPopup({ kind, key: popupKey, revisionId: row.candidate.revisionId, digest: row.candidate.artifactDigest });
  };
  const downloadedChannels = (row: typeof visible[number]) => new Set(downloads?.entries.flatMap((entry) =>
    entry.targetKey === targetKey && entry.choice.revisionId === row.candidate.revisionId
      && entry.choice.contributionId === row.candidate.contributionId && entry.choice.artifactDigest === row.candidate.artifactDigest
      && (entry.status === "imported" || entry.status === "reused") ? entry.choice.channels : []));
  const downloadOptions = (row: typeof visible[number]) => {
    const downloaded = downloadedChannels(row);
    return compatibleChannels(row.candidate, row.channels).filter((value) => !downloaded.has(value));
  };
  const startDownload = (row: typeof visible[number], requested: ResultChannel[], confirmed: boolean) => {
    if (!canDownload || !targetKey || !downloads || !requested.length || requested.some((value) => !downloadOptions(row).includes(value))) return;
    const selection = selectDownload(row.candidate, targetKey, requested);
    if (selection.reviewRequired.length && !confirmed) return;
    close();
    void downloads.start(lookup.candidates, [selection], confirmed);
  };
  const requestDownload = (event: MouseEvent<HTMLButtonElement>, row: typeof visible[number]) => {
    const options = downloadOptions(row);
    if (!canDownload || !target || !options.length) return;
    if (options.length === 1 && strongChannel(row.candidate, options[0])) startDownload(row, options, false);
    else open(event, row, "download");
  };
  const loading = lookup.status === "loading";

  return <section className="community-filter-results" aria-label="Community filters">
    <header className="community-results-header"><h3>Community Filters</h3>
      <div className="button-row">
        <button className="btn-secondary" type="button" disabled={downloads?.running || loading || tracks?.loading || tracks?.disabled} onClick={() => void lookup.refresh()}>
          {lookup.response ? "Refresh community filters" : "Find community filters"}</button>
        {loading && <button className="btn-secondary" type="button" onClick={lookup.cancel}>Cancel lookup</button>}
      </div></header>
    {tracks && <AudioTrackField {...tracks} disabled={tracks.disabled || downloads?.running} />}
    {loading && <p role="status">Looking up community filters…</p>}
    {lookup.status === "cancelled" && <p role="status">Community lookup cancelled.</p>}
    {lookup.error && <p role="alert">{errors[lookup.error.code] || "Community lookup is unavailable."}</p>}
    {lookup.stale && <p role="status">Previous results are stale.</p>}
    {lookup.response?.warnings.length ? <details className="community-lookup-warnings">
      <summary tabIndex={0}>Lookup notes</summary><ul>{lookup.response.warnings.map((warning, index) =>
        <li key={`${warning}:${index}`}>{warning.replace(/_/g, " ")}</li>)}</ul>
    </details> : null}
    {lookup.candidates.length > 0 && <>
      <div className="community-result-filters">
        <CommunityMatchFilter bands={bands} counts={matchBandCounts(lookup.candidates, channel)} onChange={setBands} />
        <label>Channel <select aria-label="Channel" value={channel} onChange={(event) => setChannel(event.target.value as ChannelFilter)}>
          <option value="all">Any channel</option><option value="audio">Audio</option>
          <option value="video">Video</option><option value="both">Audio and video</option>
        </select></label>
        <span role="status" className="muted-copy community-result-total">{visible.length} {visible.length === 1 ? "result" : "results"}</span>
      </div>
      {visible.length ? <div className="community-results-scroll">
        <table className="community-results-table">
          <caption className="visually-hidden">Community filter matches</caption>
          <thead><tr>{columns.map(({ id, label }) => <th scope="col" key={id}
            className={id === "filters" ? "community-filter-count" : undefined} aria-sort={sort.column === id ? sort.direction : "none"}>
            <button type="button" className="community-sort-button" onClick={() => setSort({ column: id,
              direction: sort.column === id ? sort.direction === "ascending" ? "descending" : "ascending" : id === "filters" ? "descending" : "ascending" })}>
              {label}<CommunityIcon name={sort.column === id ? sort.direction : "sort"} />
            </button></th>)}
            <th scope="col" className="community-actions-column"><span className="visually-hidden">Actions</span></th></tr></thead>
          <tbody>{visible.map((row) => {
            const { candidate } = row;
            const compatible = compatibleChannels(candidate, row.channels);
            const downloaded = downloadedChannels(row);
            return <CommunityFilterRow key={JSON.stringify([candidate.revisionId, candidate.artifactDigest])} {...row} disabled={!ready}
              refreshKey={JSON.stringify([lookup.key, targetKey, candidate.artifactDigest])}
              onDetails={(event) => open(event, row, "details")}
              download={downloads?.allowed ? { available: canDownload && compatible.some((value) => !downloaded.has(value)),
                complete: compatible.length > 0 && compatible.every((value) => downloaded.has(value)),
                onClick: (event) => requestDownload(event, row) } : undefined} />;
          })}</tbody>
        </table>
      </div> : <p>No results match these filters.</p>}
    </>}
    {lookup.status === "ready" && !lookup.candidates.length && <p role="status">No community filters were found.</p>}
    {lookup.response?.page?.nextCursor && <button className="btn-secondary" type="button" disabled={!ready || downloads?.running} onClick={() => void lookup.more()}>More community results</button>}
    {downloads && <CommunityDownloadPanel downloads={downloads} candidates={lookup.candidates} targetKey={targetKey} disabled={!ready} />}
    {popupRow && target && (popup?.kind === "details"
      ? <CommunityFilterDetails candidate={popupRow.candidate} channels={popupRow.channels} target={target}
        onClose={close} returnFocusRef={returnFocus} />
      : canDownload && <CommunityFilterDownloadDialog candidate={popupRow.candidate} channels={downloadOptions(popupRow)} target={target}
        onClose={close} returnFocusRef={returnFocus} onDownload={(selected, confirmed) => startDownload(popupRow, selected, confirmed)} />)}
  </section>;
}
