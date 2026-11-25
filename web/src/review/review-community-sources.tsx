import type { ReviewDraft } from "./review-draft";
import { setCommunityIncluded, stageCommunityRefresh, type CommunityEntry } from "./review-community";

/** Why an entry can't be included or refreshed now, or null. */
function blockedReason(entry: CommunityEntry, maySearch: boolean): string | null {
  if (entry.source) {
    if (!entry.link || entry.source.state === "link_unavailable") return "Its community filter set is no longer available, so it can't be refreshed. Its filters stay in this review.";
    if (entry.link.alignmentState === "required") return "Align these filters in the Community tab before refreshing them.";
    return maySearch ? null : "Your account can't read community filters, so it can't refresh them.";
  }
  if (entry.link?.alignmentState === "required") return "Align these filters in the Community tab before including them.";
  return null;
}

function entryStatus(entry: CommunityEntry, blocked: string | null): string | null {
  if (!entry.included) return entry.source ? "Removed with its filters when you save." : blocked;
  if (!entry.source) return "Saving the review adds these community filters to the results. Low-confidence ones start disabled.";
  if (entry.refreshStaged) return "Refresh staged. Save the review to bring in the community changes; your edits are kept.";
  if (entry.changed && !blocked) return "Community filters changed since included.";
  return blocked;
}

/**
 * The "Community filters" entries under Included scans. Including one adds the master set's filters
 * to the review's results as normal filters when the review is saved; later community changes only
 * show a "changed" notice until the user refreshes. Merged or unavailable sets can still be included.
 */
export function ReviewCommunityEntries({ entries, draft, disabled, maySearch, onChange, votesFailed, onRetryVotes }: {
  entries: CommunityEntry[]; draft: ReviewDraft; disabled: boolean; maySearch: boolean;
  onChange: (draft: ReviewDraft) => void;
  /** Whether the votes of an included snapshot's link failed to load; `onRetryVotes` loads them again. */
  votesFailed?: (linkId: string | null) => boolean; onRetryVotes?: () => void;
}) {
  return <>{entries.map((entry) => {
    const blocked = blockedReason(entry, maySearch);
    const status = entryStatus(entry, blocked);
    const statusId = `community-entry-${entry.key}-status`;
    const cannotAdd = !entry.included && !entry.source && blocked !== null;
    const stale = entry.link && (entry.link.linkState === "merged" || entry.link.linkState === "unavailable");
    const voteRetry = entry.included && !!entry.source && !!votesFailed?.(entry.source.link_id) && !!onRetryVotes;
    const offerRefresh = entry.included && entry.source && entry.changed && !entry.refreshStaged && !blocked;
    return <span className="review-scan-source review-community-source" key={entry.key}>
      <label><input type="checkbox" checked={entry.included} disabled={disabled || cannotAdd}
        aria-describedby={status ? statusId : undefined}
        onChange={(event) => onChange(setCommunityIncluded(draft, entry, event.target.checked))} />
        <span>Community filters · {entry.label}</span></label>
      {stale && <span className="master-badge master-link-state">{entry.link!.linkState === "merged" ? "Merged" : "Unavailable"}</span>}
      {status && <small id={statusId} className={offerRefresh ? "review-community-changed" : "muted-copy"}>{status}</small>}
      {offerRefresh && <button type="button" className="btn-secondary compact-button" disabled={disabled}
        onClick={() => onChange(stageCommunityRefresh(draft, entry))}>Refresh community filters</button>}
      {voteRetry && <>
        <small className="master-inline-error">Couldn’t load your votes on these filters.</small>
        <button type="button" className="btn-secondary compact-button" onClick={onRetryVotes}>Retry loading votes</button>
      </>}
    </span>;
  })}</>;
}
