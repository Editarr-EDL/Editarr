import type { ReviewDraft } from "./review-draft";
import { entryFilters, setCommunityIncluded, stageCommunityRefresh, type CommunityEntry } from "./review-community";
import { filterName, percent } from "../scans/community-master-format";
import { formatTime } from "../shared/display";

/** Why an entry can't be included or refreshed now, or null. */
function blockedReason(entry: CommunityEntry, maySearch: boolean): string | null {
  if (entry.source) {
    if (!entry.link || entry.source.state === "link_unavailable") return "Its community filter set is no longer available, so it can't be refreshed. The stored filters stay in this review.";
    if (entry.link.alignmentState === "required") return "Align these filters in the Community tab before refreshing them.";
    return maySearch ? null : "Your account can't read community filters, so it can't refresh them.";
  }
  if (entry.link?.alignmentState === "required") return "Align these filters in the Community tab before including them.";
  return null;
}

function entryStatus(entry: CommunityEntry, blocked: string | null): string | null {
  if (!entry.included) return entry.source ? "Removed when you save." : blocked;
  if (!entry.source) return "Saving the review stores these filters as they are now.";
  if (entry.refreshStaged) return "Refresh staged. Save the review to store the current filters.";
  if (entry.changed && !blocked) return "Community filters changed since included.";
  return blocked;
}

/**
 * The "Community filters" entries under Included scans. Including one stores a snapshot of the master
 * set's active filters when the review is saved; later Community tab changes only show a "changed"
 * notice until the user refreshes. Merged or unavailable sets can still be included.
 */
export function ReviewCommunityEntries({ entries, draft, disabled, maySearch, onChange }: {
  entries: CommunityEntry[]; draft: ReviewDraft; disabled: boolean; maySearch: boolean;
  onChange: (draft: ReviewDraft) => void;
}) {
  return <>{entries.map((entry) => {
    const blocked = blockedReason(entry, maySearch);
    const status = entryStatus(entry, blocked);
    const statusId = `community-entry-${entry.key}-status`;
    const cannotAdd = !entry.included && !entry.source && blocked !== null;
    const stale = entry.link && (entry.link.linkState === "merged" || entry.link.linkState === "unavailable");
    return <span className="review-scan-source review-community-source" key={entry.key}>
      <label><input type="checkbox" checked={entry.included} disabled={disabled || cannotAdd}
        aria-describedby={status ? statusId : undefined}
        onChange={(event) => onChange(setCommunityIncluded(draft, entry, event.target.checked))} />
        <span>Community filters · {entry.label}</span></label>
      {stale && <span className="master-badge master-link-state">{entry.link!.linkState === "merged" ? "Merged" : "Unavailable"}</span>}
      {status && <small id={statusId} className={entry.changed && entry.included && !entry.refreshStaged && !blocked
        ? "review-community-changed" : "muted-copy"}>{status}</small>}
      {entry.included && !!entry.source?.unusable_filter_ids?.length && <small className="muted-copy">
        {entry.source.unusable_filter_ids.length} {entry.source.unusable_filter_ids.length === 1 ? "filter has" : "filters have"} an
        invalid classification and {entry.source.unusable_filter_ids.length === 1 ? "is" : "are"} left out of exports.</small>}
      {entry.included && entry.source && entry.changed && !entry.refreshStaged && !blocked
        && <button type="button" className="btn-secondary compact-button" disabled={disabled}
          onClick={() => onChange(stageCommunityRefresh(draft, entry))}>Refresh community filters</button>}
    </span>;
  })}</>;
}

/** Read-only results for the community filters the draft includes, next to the scan results. */
export function ReviewCommunityResults({ entries }: { entries: CommunityEntry[] }) {
  const rows = entries.flatMap((entry) => {
    const { filters, preview } = entryFilters(entry);
    return filters.map((filter) => ({ entry, filter, preview }));
  }).sort((a, b) => a.filter.start_seconds - b.filter.start_seconds);
  if (!entries.some((entry) => entry.included)) return null;
  return <section className="finding-group review-community-results" aria-labelledby="review-community-results-heading">
    <div className="section-heading"><div>
      <h3 id="review-community-results-heading">Community filters</h3>
      <p>{rows.length} {rows.length === 1 ? "result" : "results"} from community filter sets. Change them in the Community tab, then refresh them here.</p>
    </div></div>
    {rows.length > 0 && <div className="library-table-wrap" role="region" tabIndex={0} aria-label="Community review filters table">
      <table className="scan-filter-table review-community-table">
        <thead><tr><th scope="col">Time</th><th scope="col">Filter</th><th scope="col">Severity</th>
          <th scope="col">Action</th><th scope="col">Confidence</th><th scope="col">Source</th></tr></thead>
        <tbody>{rows.map(({ entry, filter, preview }) => <tr key={`${entry.key}:${filter.filter_id}`}>
          <td data-label="Time">{formatTime(filter.start_seconds)}–{formatTime(filter.end_seconds)}</td>
          <th scope="row" data-label="Filter">{filterName(filter)}
            {entry.source?.unusable_filter_ids?.includes(filter.filter_id) && <small className="muted-copy"> (not exported)</small>}</th>
          <td data-label="Severity">{filter.severity || "—"}</td>
          <td data-label="Action">{filter.edit_action === "mute" ? "Mute" : "Skip"}</td>
          <td data-label="Confidence">{filter.confidence === null ? "—" : percent(filter.confidence)}</td>
          <td data-label="Source">Community · {entry.label}{preview && <small className="muted-copy"> (not saved yet)</small>}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </section>;
}
