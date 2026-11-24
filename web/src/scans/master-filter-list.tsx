import { useId, useState } from "react";
import { decideMasterFilter } from "../api/api-community-masters";
import type { MasterFilter, MasterPreferences, MasterView } from "../api/api-community-master-types";
import { ConfidenceBadge, LocallyVerifiedIcon } from "./community-master-badges";
import { filterActive, filterName, filterVisible, formatRange, masterErrorText, percent } from "./community-master-format";
import { MasterVoteButtons } from "./master-vote-buttons";

const stateLabels: Record<MasterFilter["state"], string> = {
  pending: "From community", verified: "Confirmed", adjusted: "Adjusted", rejected: "Rejected", low_confidence: "Low Confidence",
};

/** Threshold slider and the "Show Low Confidence filters" toggle (off by default). */
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
  </div>;
}

/**
 * Master set filter rows filtered by the user's preferences. Touched rows always show. Each row has
 * its confidence, contributor count (never names), the Locally verified icon, votes and local decisions.
 */
export function MasterFilterList({ view, preferences, canVote, canDecide, onFilter }: {
  view: MasterView; preferences: MasterPreferences; canVote: boolean; canDecide: boolean;
  onFilter: (filter: MasterFilter) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { link } = view;
  const alignmentRequired = link.alignmentState === "required";
  const visible = view.filters.filter((filter) => filterVisible(filter, preferences))
    .sort((a, b) => a.effective.startMs - b.effective.startMs);
  const hidden = view.filters.length - visible.length;

  const decide = async (filter: MasterFilter, decision: "verify" | "reject" | "reset") => {
    setBusy(filter.filterId);
    setError(null);
    try { onFilter(await decideMasterFilter(link.linkId, filter.filterId, { decision, expected_state: filter.state })); }
    catch (reason) { setError(masterErrorText(reason)); }
    finally { setBusy(null); }
  };

  return <div className="master-filter-list">
    <p className="muted-copy" role="status">{visible.length} {visible.length === 1 ? "filter" : "filters"} shown
      {hidden > 0 && ` · ${hidden} hidden by your confidence settings`}</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    {visible.length > 0 && <div className="community-results-scroll" role="region" aria-label="Community filters" tabIndex={0}>
      <table className="community-results-table master-filter-table">
        <thead><tr><th scope="col">Time</th><th scope="col">Filter</th><th scope="col">Confidence</th>
          <th scope="col">Contributors</th><th scope="col">Status</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr></thead>
        <tbody>{visible.map((filter) => {
          const name = `${filterName({ category: filter.category, subcategory: filter.effective.subcategory })} at ${formatRange(filter.effective)}`;
          const active = filterActive(filter, preferences, alignmentRequired);
          return <tr key={filter.filterId} className={active ? undefined : "master-row-inactive"}>
            <td className="master-time">{formatRange(filter.effective)}</td>
            <th scope="row">{filterName({ category: filter.category, subcategory: filter.effective.subcategory })}
              <small className="muted-copy">{[filter.effective.severity, filter.effective.action].filter(Boolean).join(" · ")}</small></th>
            <td><ConfidenceBadge confidence={filter.master.confidence} low={filter.master.lowConfidence} /></td>
            <td aria-label={`${filter.master.contributorCount} contributors`}>{filter.master.contributorCount}</td>
            <td><span className="master-status">{stateLabels[filter.state]}{filter.locallyVerified && <LocallyVerifiedIcon />}
              {!active && <small className="muted-copy">Not applied</small>}</span></td>
            <td><div className="community-row-actions">
              {canVote && <MasterVoteButtons linkId={link.linkId} filterId={filter.filterId} name={name} myVote={filter.myVote}
                onVoted={(myVote) => onFilter({ ...filter, myVote, touched: filter.touched || myVote !== null })} />}
              {canDecide && filter.state !== "verified" && filter.state !== "rejected" && <button type="button"
                className="btn-secondary compact-button" disabled={busy !== null} aria-label={`Confirm ${name}`}
                onClick={() => void decide(filter, "verify")}>Confirm</button>}
              {canDecide && filter.state !== "rejected" && <button type="button" className="btn-secondary compact-button"
                disabled={busy !== null} aria-label={`Reject ${name}`} onClick={() => void decide(filter, "reject")}>Reject</button>}
              {canDecide && filter.touched && filter.state !== "pending" && filter.state !== "low_confidence" && <button type="button"
                className="btn-secondary compact-button" disabled={busy !== null} aria-label={`Reset ${name}`}
                onClick={() => void decide(filter, "reset")}>Reset</button>}
            </div></td>
          </tr>;
        })}</tbody>
      </table>
    </div>}
  </div>;
}
