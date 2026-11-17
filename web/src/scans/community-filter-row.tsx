import { useId, useState, type MouseEvent } from "react";
import type { MatchBand, RemoteFilterCandidate } from "../api/api-community-lookup";
import { CommunityFeedback } from "./community-feedback";
import { matchReason } from "./community-filter-details";
import { CommunityIcon } from "./community-icon";
import { bandLabels, categoryCounts, categoryLabel, resultChannels, resultName, strongChannel, type ResultChannel } from "./community-result-state";

type Action = (event: MouseEvent<HTMLButtonElement>) => void;
export function CommunityFilterRow({ candidate, band, channels, count, disabled, refreshKey, download, onDetails }: {
  candidate: RemoteFilterCandidate; band: MatchBand | null; channels: ResultChannel[]; count: number | null;
  disabled: boolean; refreshKey: string;
  download?: { available: boolean; complete: boolean; onClick: Action };
  onDetails: Action;
}) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  const name = resultName(candidate);
  const summary = candidate.summary;
  const allChannels = resultChannels(candidate);
  const categories = expanded ? categoryCounts(summary.taxonomy) : [];
  const ready = channels.length > 0 && channels.every((channel) => strongChannel(candidate, channel));
  return <>
    <tr>
      <th scope="row">
        <button className="community-result-expand" type="button" aria-expanded={expanded} aria-controls={id}
          onClick={() => setExpanded(!expanded)}><CommunityIcon name="expand" /><span>{name}
          <small className="muted-copy">{candidate.attribution.displayName}{count !== null && <span className="community-count-inline"> · {count} filters</span>}</small>
        </span></button>
      </th>
      <td><span className={ready ? "community-strength" : band && band !== "incompatible" ? "community-needs-review" : "muted-copy"}>{band ? bandLabels[band] : "Unknown"}</span></td>
      <td className="community-filter-count">{count ?? "Unknown"}</td>
      <td><div className="community-row-actions">
        <CommunityFeedback key={refreshKey} revisionId={candidate.revisionId} refreshKey={refreshKey} authorId={candidate.attribution.contributorId}
          disabled={disabled} totals={candidate.statistics} />
        {download && <button className="community-icon-button community-download-icon" type="button"
          aria-label={`${download.complete ? "Downloaded" : "Download"} ${name}`}
          title={download.complete ? "Downloaded" : !download.available && !disabled ? "No compatible filters available" : "Download filters"}
          disabled={disabled || !download.available} onClick={download.onClick}><CommunityIcon name={download.complete ? "complete" : "download"} /></button>}
      </div></td>
    </tr>
    <tr className="community-result-expanded" id={id} hidden={!expanded}>
      <td colSpan={4}>{expanded && <div className="community-expanded-content">
        <div className="community-expanded-heading">
          <strong>{summary.total} {summary.total === 1 ? "filter" : "filters"}
            {allChannels.length > 0 && <span className="muted-copy"> · {allChannels.map((channel) => `${summary.channels[channel] ?? "Unknown"} ${channel}`).join(" · ")}</span>}</strong>
          <button className="community-details-link" aria-label={`Details for ${name}`} type="button" onClick={onDetails}>Details</button>
        </div>
        {channels.map((channel) => {
          const result = candidate.compatibility[channel];
          return <p key={channel} className="muted-copy">{categoryLabel(channel)} · {bandLabels[result.band]}
            {result.conflicts[0] && ` · ${matchReason(result.conflicts[0])}`}</p>;
        })}
        {categories.length > 0 && <>
          <p className="community-category-heading">{channels.length < allChannels.length ? "All filters by category" : "Filters by category"}</p>
          <dl className="community-category-counts">{categories.map(({ label, total, items }) => <div key={label}>
            <dt>{label} ({total})</dt>{items.length > 0 && <dd>{items.map((item) => `${item.label} ${item.count}`).join(" · ")}</dd>}
          </div>)}</dl>
        </>}
      </div>}</td>
    </tr>
  </>;
}
