import type { SavedReview } from "../api/api-review-types";
import { ConfidenceBadge, LocallyVerifiedIcon } from "../scans/community-master-badges";
import { isCommunityFilter } from "./review-community";

type Filter = SavedReview["filters"][number];

/** How many of a review's filters came from community snapshots. */
export const communityResultCount = (filters: Filter[]) => filters.filter(isCommunityFilter).length;

/**
 * Source details of a result row's community filters: the community version, its confidence badge,
 * contributor count and Locally verified icon, plus "Edited" and "Removed from community" flags.
 * Renders nothing for scan filters.
 */
export function CommunityFilterSource({ filters }: { filters: Filter[] }) {
  const community = filters.filter((filter) => filter.community);
  if (!community.length) return null;
  return <span className="review-community-attribution">{community.map((filter) => {
    const info = filter.community!;
    return <span className="review-community-filter" key={filter.id}>
      <span className="review-community-label">Community · {info.label}</span>
      {info.confidence !== null && <ConfidenceBadge confidence={info.confidence} low={info.low_confidence} />}
      {info.contributor_count !== null && <small className="muted-copy">
        {info.contributor_count} {info.contributor_count === 1 ? "contributor" : "contributors"}</small>}
      {info.locally_verified && <LocallyVerifiedIcon />}
      {info.edited && <span className="master-badge review-community-edited">Edited</span>}
      {info.removed && <span className="master-badge review-community-removed">Removed from community</span>}
    </span>;
  })}</span>;
}
