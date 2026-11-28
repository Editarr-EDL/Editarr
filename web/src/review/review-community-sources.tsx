import { useRef } from "react";
import type { ReviewDraft } from "./review-draft";
import { ReviewSourceRow } from "./review-source-row";
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
  if (!entry.source) return "Edit and preview these filters in the draft. Save to keep your changes.";
  if (entry.refreshStaged) return "Refresh staged. Save the review to bring in the community changes; your edits are kept.";
  if (entry.changed && !blocked) return "Community filters changed since included.";
  return blocked;
}

/** The muted details line of a community entry: its filter count, revision and alignment, as known. */
function entryDetails(entry: CommunityEntry): string {
  const count = entry.source?.filter_count;
  const revision = entry.source?.master_revision ?? entry.link?.appliedRevision;
  return ["Community",
    count !== undefined && `${count} ${count === 1 ? "filter" : "filters"}`,
    revision !== undefined && `revision ${revision}`,
    entry.link && (entry.link.alignmentState === "required" ? "needs alignment" : "aligned"),
  ].filter(Boolean).join(" · ");
}

/** Whether an included community entry has changes to refresh, shown as a dot on the Sources button. */
export const communityEntryChanged = (entry: CommunityEntry) => entry.included && entry.changed && !entry.refreshStaged;

/**
 * The community entries of the Included scans popover. Including one adds the master set's filters to the
 * draft's results as normal editable filters; later community changes only show a "changed"
 * notice until the user refreshes. Merged or unavailable sets can still be included.
 */
export function ReviewCommunityEntries({ entries, draft, disabled, maySearch, onChange, votesFailed, onRetryVotes }: {
  entries: CommunityEntry[]; draft: ReviewDraft; disabled: boolean; maySearch: boolean;
  onChange: (draft: ReviewDraft) => void;
  /** Whether the votes of an included snapshot's link failed to load; `onRetryVotes` loads them again. */
  votesFailed?: (linkId: string | null) => boolean; onRetryVotes?: () => void;
}) {
  const checkboxes = useRef(new Map<string, HTMLInputElement>());
  // An action that removes itself hands focus back to its entry's checkbox rather than losing it.
  const refocus = (key: string) => requestAnimationFrame(() => checkboxes.current.get(key)?.focus());
  return <>{entries.map((entry) => {
    const blocked = blockedReason(entry, maySearch);
    const status = entryStatus(entry, blocked);
    const statusId = `community-entry-${entry.key}-status`;
    const cannotAdd = !entry.included && !entry.source && blocked !== null;
    const stale = entry.link && (entry.link.linkState === "merged" || entry.link.linkState === "unavailable");
    const voteRetry = entry.included && !!entry.source && !!votesFailed?.(entry.source.link_id) && !!onRetryVotes;
    const offerRefresh = entry.included && entry.source && entry.changed && !entry.refreshStaged && !blocked;
    return <ReviewSourceRow key={entry.key} kind="community" name={`Community · ${entry.label}`} details={entryDetails(entry)}
      included={entry.included} disabled={disabled || cannotAdd} describedBy={status ? statusId : undefined}
      checkboxRef={(element) => { if (element) checkboxes.current.set(entry.key, element); else checkboxes.current.delete(entry.key); }}
      onChange={(included) => onChange(setCommunityIncluded(draft, entry, included))}>
      {stale && <span className="master-badge master-link-state">{entry.link!.linkState === "merged" ? "Merged" : "Unavailable"}</span>}
      {status && <small id={statusId} className={offerRefresh ? "review-community-changed" : "muted-copy"}>
        {offerRefresh && <span className="review-sources-dot" aria-hidden="true" />}{status}</small>}
      {offerRefresh && <button type="button" className="btn-secondary compact-button" disabled={disabled}
        onClick={() => { onChange(stageCommunityRefresh(draft, entry)); refocus(entry.key); }}>Refresh community filters</button>}
      {voteRetry && <>
        <small className="master-inline-error">Couldn’t load your votes on these filters.</small>
        <button type="button" className="btn-secondary compact-button" onClick={onRetryVotes}>Retry loading votes</button>
      </>}
    </ReviewSourceRow>;
  })}</>;
}
