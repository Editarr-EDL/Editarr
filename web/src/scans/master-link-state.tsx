import { useState } from "react";
import type { LinkState, MasterLinkSummary, MasterTarget } from "../api/api-community-master-types";
import { OTHER, type PickerValue } from "./community-master-format";
import { MasterFindResults } from "./master-find-results";

const stateText: Partial<Record<LinkState, { badge: string; detail: string }>> = {
  merged: {
    badge: "Merged",
    detail: "The community merged this version into another one. Your filters and decisions here are kept, but they no longer get updates. Find again to switch to the merged version.",
  },
  unavailable: {
    badge: "Unavailable",
    detail: "The community no longer offers this version. Your filters and decisions here are kept, but they no longer get updates. Find again to choose another version.",
  },
};

/** Whether a link's community version stopped receiving updates (merged or unavailable). */
export const linkStale = (state: LinkState) => state in stateText;

export function MasterLinkStateBadge({ state }: { state: LinkState }) {
  const text = stateText[state];
  return text ? <span className="master-badge master-link-state">{text.badge}</span> : null;
}

/** The version picker value that searches for the link's own version label. */
export function linkPickerValue(link: Pick<MasterLinkSummary, "aliasId" | "label">): PickerValue {
  if (link.aliasId) return { selection: link.aliasId, otherText: "" };
  return { selection: OTHER, otherText: link.label === "Other" ? "" : link.label };
}

/**
 * Notice for a merged or unavailable link with a "Find again" action, which searches inline for
 * community versions of the same local version, starting from the link's version label.
 */
export function MasterLinkStateNotice({ link, target }: { link: MasterLinkSummary; target: MasterTarget }) {
  const [finding, setFinding] = useState(false);
  const text = stateText[link.linkState];
  if (!text) return null;
  return <div className="community-notice master-link-state-notice">
    <p>{text.detail}</p>
    {finding ? <MasterFindResults target={target} findOnOpen initialLabel={linkPickerValue(link)} />
      : <button type="button" className="btn-secondary compact-button" onClick={() => setFinding(true)}>Find again</button>}
  </div>;
}
