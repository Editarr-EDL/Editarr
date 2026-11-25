import { InfoButton } from "../ui/InfoButton";
import { percent, UNCONFIRMED_TOOLTIP } from "./community-master-format";

/** "Unconfirmed" label whose hover/focus tooltip explains candidate versions. */
export function UnconfirmedLabel() {
  return <InfoButton label="Unconfirmed" content="Unconfirmed" className="master-badge master-unconfirmed">
    {UNCONFIRMED_TOOLTIP}
  </InfoButton>;
}

/** Small check icon with a "Locally verified" hover/focus tooltip. */
export function LocallyVerifiedIcon() {
  return <InfoButton label="Locally verified" className="master-verified-icon" content={
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>
  }>Locally verified</InfoButton>;
}

export function MatchPercent({ value }: { value: number }) {
  return <span className={`master-badge master-match${value >= 65 ? " master-match-strong" : ""}`}>{value}% match</span>;
}

export const LOW_CONFIDENCE_TOOLTIP = "More people disputed, voted down or didn't find this filter than confirmed or voted it up. "
  + "Low Confidence filters stay hidden here, and start disabled in reviews, unless you show them.";

/** Confidence badge; Low Confidence rows are labelled explicitly and explained in a tooltip. */
export function ConfidenceBadge({ confidence, low }: { confidence: number; low: boolean }) {
  return <span className="master-confidence-cell">
    <span className={`master-badge master-confidence${low ? " master-low-confidence" : ""}`}>
      {low ? `Low Confidence · ${percent(confidence)}` : `${percent(confidence)} confidence`}
    </span>
    {low && <InfoButton label="About Low Confidence">{LOW_CONFIDENCE_TOOLTIP}</InfoButton>}
  </span>;
}
