import type { MasterLinkSummary } from "../api/api-community-master-types";
import type { ReviewCommunityMembership, ReviewCommunitySource, ReviewFilter, SavedReview } from "../api/api-review-types";
import { reviewDraftFilters, type ReviewDraft } from "./review-draft";

/** One "Community filters" entry under Included scans: a stored snapshot, a linkable master set, or both. */
export interface CommunityEntry {
  key: string;
  label: string;
  /** The account's live master link, when it may be read. */
  link: MasterLinkSummary | null;
  /** The stored snapshot, when the saved review includes one. */
  source: ReviewCommunitySource | null;
  included: boolean;
  refreshStaged: boolean;
  /** Whether the link's served filters differ from the stored snapshot, so Refresh would bring changes in. */
  changed: boolean;
}

const membershipFor = (draft: ReviewDraft, entry: Pick<CommunityEntry, "link" | "source">) =>
  draft.communityMemberships.find((value) => (entry.source && value.source_id === entry.source.source_id)
    || (entry.link && value.link_id === entry.link.linkId));

/** Whether a review filter came from a community snapshot. */
export const isCommunityFilter = (filter: Pick<ReviewFilter, "community_source_id">) => !!filter.community_source_id;

/**
 * Build the Included scans entries from stored snapshots and the account's links for this version.
 * A link the saved review already has a snapshot of is folded into that snapshot's entry.
 */
export function communityEntries(draft: ReviewDraft, sources: ReviewCommunitySource[], links: MasterLinkSummary[]): CommunityEntry[] {
  const entries: Array<Pick<CommunityEntry, "source" | "link">> = sources.map((source) => ({
    source, link: links.find((link) => link.linkId === source.link_id) ?? null }));
  for (const link of links) {
    if (link.linkState !== "replaced" && !entries.some((entry) => entry.link?.linkId === link.linkId)) entries.push({ source: null, link });
  }
  return entries.map(({ source, link }) => {
    const membership = membershipFor(draft, { source, link });
    return {
      key: source?.source_id ?? link!.linkId, label: source?.label ?? link!.label, link, source,
      included: !!membership, refreshStaged: !!membership && "refresh" in membership && !!membership.refresh,
      changed: source?.state === "changed",
    };
  });
}

const removedKey = (sourceId: string) => `community:${sourceId}`;

/**
 * Include or remove one entry. Removing a stored snapshot also takes its filters out of the draft
 * (kept for this editing session); re-including it restores them unchanged.
 */
export function setCommunityIncluded(draft: ReviewDraft, entry: CommunityEntry, included: boolean): ReviewDraft {
  const rest = draft.communityMemberships.filter((value) => value !== membershipFor(draft, entry));
  const sourceId = entry.source?.source_id;
  if (!included) {
    if (!sourceId) {
      const linkId = entry.link!.linkId;
      const { [linkId]: _removed, ...communityDraftTokens } = draft.communityDraftTokens;
      const filterBases = Object.fromEntries(Object.entries(draft.filterBases).filter(
        ([, pair]) => pair.base.community_source_id !== linkId,
      ));
      return { ...draft, communityMemberships: rest, communityDraftTokens,
        filterBases, filters: draft.filters.filter((filter) => filter.community_source_id !== linkId) };
    }
    const removed = draft.filters.filter((filter) => filter.community_source_id === sourceId);
    return {
      ...draft, communityMemberships: rest,
      filters: draft.filters.filter((filter) => filter.community_source_id !== sourceId),
      removedSnapshots: removed.length ? { ...draft.removedSnapshots, [removedKey(sourceId)]: removed } : draft.removedSnapshots,
    };
  }
  const next: ReviewCommunityMembership = sourceId ? { source_id: sourceId } : { link_id: entry.link!.linkId };
  if (!sourceId) return { ...draft, communityMemberships: [...rest, next] };
  const { [removedKey(sourceId)]: restored = [], ...removedSnapshots } = draft.removedSnapshots;
  return { ...draft, communityMemberships: [...rest, next], filters: [...draft.filters, ...restored], removedSnapshots };
}

/** Materialize the newly included link only if this response still belongs to the active draft. */
export function loadCommunityDraft(draft: ReviewDraft, linkId: string, token: string, filters: ReviewFilter[]): ReviewDraft {
  if (draft.communityDraftTokens[linkId] || !draft.communityMemberships.some((item) => item.link_id === linkId)) return draft;
  return { ...draft, filters: [...draft.filters, ...filters],
    communityDraftTokens: { ...draft.communityDraftTokens, [linkId]: token } };
}

/** Keep draft link IDs out of the stored-snapshot field of the save request. */
export function communitySaveFilters(draft: ReviewDraft) {
  const filters = reviewDraftFilters(draft);
  const communityDrafts = Object.entries(draft.communityDraftTokens).map(([linkId, token]) => ({
    link_id: linkId, token, filters: filters.filter((filter) => filter.community_source_id === linkId),
  }));
  return {
    filters: filters.filter((filter) => !filter.community_source_id || !draft.communityDraftTokens[filter.community_source_id]),
    ...(communityDrafts.length ? { community_drafts: communityDrafts } : {}),
  };
}

/** Stage refreshing an included stored entry: saving takes a new snapshot and merges it into the review's filters. */
export function stageCommunityRefresh(draft: ReviewDraft, entry: CommunityEntry): ReviewDraft {
  if (!entry.source) return draft;
  const sourceId = entry.source.source_id;
  return { ...draft, communityMemberships: draft.communityMemberships.map((value) => value.source_id === sourceId
    ? { source_id: sourceId, refresh: true } : value) };
}

/** Whether a saved review has evidence to export: a scan, or a community filter. */
export function hasExportEvidence(review: Pick<SavedReview, "memberships" | "filters">): boolean {
  return review.memberships.length > 0 || review.filters.some(isCommunityFilter);
}
