import { useEffect, useId, useRef, useState } from "react";
import type { MasterFilter, MasterPreferences, MasterView } from "../api/api-community-master-types";
import { ConfidenceBadge, LocallyVerifiedIcon } from "./community-master-badges";
import { filterName, filterVisible, formatRange, percent } from "./community-master-format";
import { MasterVoteButtons } from "./master-vote-buttons";

/** Threshold slider and the "Show Low Confidence filters" and "Hide downvoted filters" toggles (both
 * off by default). The threshold and Low Confidence decide which filters start enabled when a review
 * includes or refreshes them; hiding downvoted filters only affects the list. */
export function MasterPreferenceControls({ preferences, disabled, onChange }: {
  preferences: MasterPreferences; disabled?: boolean; onChange: (value: MasterPreferences) => void;
}) {
  const sliderId = useId();
  return <div className="master-preferences">
    <div className="master-threshold">
      <label htmlFor={sliderId}>Minimum confidence</label>
      <input id={sliderId} type="range" min={0} max={1} step={0.05} value={preferences.confidenceThreshold} disabled={disabled}
        aria-valuetext={percent(preferences.confidenceThreshold)}
        onChange={(event) => onChange({ ...preferences, confidenceThreshold: Number(event.target.value) })} />
      <output htmlFor={sliderId} aria-hidden="true">{percent(preferences.confidenceThreshold)}</output>
    </div>
    <label className="master-toggle"><input type="checkbox" checked={preferences.showLowConfidence} disabled={disabled}
      onChange={(event) => onChange({ ...preferences, showLowConfidence: event.target.checked })} />
      Show Low Confidence filters</label>
    <label className="master-toggle"><input type="checkbox" checked={!!preferences.hideDownvoted} disabled={disabled}
      onChange={(event) => onChange({ ...preferences, hideDownvoted: event.target.checked })} />
      Hide downvoted filters</label>
    <p className="muted-copy master-preferences-note">Filters below the minimum confidence, and Low Confidence filters unless
      shown, start disabled when a review includes or refreshes them.</p>
  </div>;
}

/**
 * The master set's filters that start enabled in reviews under the user's preferences, read-only:
 * each row has its confidence, contributor count (never names), the Locally verified icon and votes.
 * Filters are enabled, disabled and adjusted in the reviews that include them. A row downvoted here
 * stays listed while "Hide downvoted filters" is on, so focus and context aren't lost; it hides once
 * that setting changes or the list reloads.
 */
export function MasterFilterList({ view, preferences, canVote, onFilter }: {
  view: MasterView; preferences: MasterPreferences; canVote: boolean; onFilter: (filter: MasterFilter) => void;
}) {
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set());
  const keep = (filterId: string) => setKept((current) => new Set(current).add(filterId));
  const scroller = useRef<HTMLDivElement>(null);
  const { link } = view;
  const { confidenceThreshold, showLowConfidence, hideDownvoted } = preferences;
  useEffect(() => {
    // A new set of rows starts at Time and Filter, even if a row's actions were scrolled into view.
    setKept(new Set());
    if (scroller.current) scroller.current.scrollLeft = 0;
  }, [confidenceThreshold, showLowConfidence, hideDownvoted]);
  const visible = view.filters.filter((filter) => filterVisible(filter, preferences)
    || (kept.has(filter.filterId) && filterVisible(filter, { ...preferences, hideDownvoted: false })))
    .sort((a, b) => a.master.startMs - b.master.startMs);
  const hidden = view.filters.length - visible.length;

  return <div className="master-filter-list">
    <p className="muted-copy" role="status">{visible.length} {visible.length === 1 ? "filter" : "filters"} shown
      {hidden > 0 && ` · ${hidden} hidden by your filter settings`}</p>
    {visible.length > 0 && <div ref={scroller} className="community-results-scroll" role="region" aria-label={`${link.label} filters`} tabIndex={0}>
      <table className="community-results-table master-filter-table">
        <thead><tr><th scope="col">Time</th><th scope="col">Filter</th><th scope="col">Confidence</th>
          <th scope="col">Contributors</th><th scope="col">Verified</th><th scope="col"><span className="visually-hidden">Votes</span></th></tr></thead>
        <tbody>{visible.map((filter) => {
          const label = filterName({ category: filter.category, subcategory: filter.master.subcategory });
          const name = `${label} at ${formatRange(filter.master)}`;
          return <tr key={filter.filterId}>
            <td className="master-time">{formatRange(filter.master)}</td>
            <th scope="row">{label}
              <small className="muted-copy">{[filter.master.expression && `“${filter.master.expression}”`, filter.master.severity,
                filter.master.action].filter(Boolean).join(" · ")}</small></th>
            <td><ConfidenceBadge confidence={filter.master.confidence} low={filter.master.lowConfidence} /></td>
            <td>{filter.master.contributorCount}</td>
            <td>{filter.locallyVerified ? <LocallyVerifiedIcon /> : <span className="muted-copy">—</span>}</td>
            <td><div className="community-row-actions">
              {canVote && <MasterVoteButtons linkId={link.linkId} filterId={filter.filterId} name={name} myVote={filter.myVote}
                onVoted={(myVote) => { keep(filter.filterId); onFilter({ ...filter, myVote }); }} />}
            </div></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
  </div>;
}
