import type { ActiveMasterFilters, MasterLinkSummary } from "../api/api-community-master-types";
import type { ReviewCommunityFilter, ReviewCommunityMembership, ReviewCommunitySource, SavedReview } from "../api/api-review-types";
import type { ReviewDraft } from "./review-draft";

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
  /** The link's active filters now, or null when unknown. */
  live: ReviewCommunityFilter[] | null;
  /** Whether the link's active filters differ from the stored snapshot. */
  changed: boolean;
}

/** Convert the active-filters read path (milliseconds) to snapshot filters (seconds). */
export function liveFilters(active: ActiveMasterFilters): ReviewCommunityFilter[] {
  return active.filters.map((item) => ({
    filter_id: item.filterId, category: item.category, subcategory: item.subcategory ?? "",
    severity: item.severity ?? "", edit_action: item.action === "mute" ? "mute" : "skip",
    start_seconds: item.startMs / 1000, end_seconds: item.endMs / 1000, confidence: item.confidence ?? null,
  }));
}

const signature = (filters: ReviewCommunityFilter[]) => JSON.stringify(filters.map((item) => [
  item.filter_id, item.category, Math.round(item.start_seconds * 1000), Math.round(item.end_seconds * 1000),
  item.subcategory, item.severity, item.edit_action,
]));

/** Whether two unclipped filter lists differ in anything but confidence, as the server compares them. */
export const filtersDiffer = (a: ReviewCommunityFilter[], b: ReviewCommunityFilter[]) => signature(a) !== signature(b);

const membershipFor = (draft: ReviewDraft, entry: Pick<CommunityEntry, "link" | "source">) =>
  draft.communityMemberships.find((value) => (entry.source && value.source_id === entry.source.source_id)
    || (entry.link && value.link_id === entry.link.linkId));

/**
 * Build the Included scans entries from stored snapshots and the account's links for this version.
 * A link the saved review already has a snapshot of is folded into that snapshot's entry.
 */
export function communityEntries(draft: ReviewDraft, sources: ReviewCommunitySource[], links: MasterLinkSummary[],
  live: Record<string, ReviewCommunityFilter[]>): CommunityEntry[] {
  const entries: Array<Pick<CommunityEntry, "source" | "link">> = sources.map((source) => ({
    source, link: links.find((link) => link.linkId === source.link_id) ?? null }));
  for (const link of links) {
    if (link.linkState !== "replaced" && !entries.some((entry) => entry.link?.linkId === link.linkId)) entries.push({ source: null, link });
  }
  return entries.map(({ source, link }) => {
    const membership = membershipFor(draft, { source, link });
    const current = link ? live[link.linkId] ?? null : null;
    const changed = source ? (current && link?.alignmentState !== "required" ? filtersDiffer(source.filters, current) : source.state === "changed") : false;
    return {
      key: source?.source_id ?? link!.linkId, label: source?.label ?? link!.label, link, source,
      included: !!membership, refreshStaged: !!membership && "refresh" in membership && !!membership.refresh,
      live: current, changed,
    };
  });
}

/** Include or remove one entry. Re-including a stored snapshot keeps it unchanged. */
export function setCommunityIncluded(draft: ReviewDraft, entry: CommunityEntry, included: boolean): ReviewDraft {
  const rest = draft.communityMemberships.filter((value) => value !== membershipFor(draft, entry));
  if (!included) return { ...draft, communityMemberships: rest };
  const next: ReviewCommunityMembership = entry.source ? { source_id: entry.source.source_id } : { link_id: entry.link!.linkId };
  return { ...draft, communityMemberships: [...rest, next] };
}

/** Stage taking a new snapshot of an included stored entry when the review is saved. */
export function stageCommunityRefresh(draft: ReviewDraft, entry: CommunityEntry): ReviewDraft {
  if (!entry.source) return draft;
  const sourceId = entry.source.source_id;
  return { ...draft, communityMemberships: draft.communityMemberships.map((value) => value.source_id === sourceId
    ? { source_id: sourceId, refresh: true } : value) };
}

/** The filters an entry contributes to the draft's results: the stored snapshot, or a preview of
 * the snapshot that saving will take. */
export function entryFilters(entry: CommunityEntry): { filters: ReviewCommunityFilter[]; preview: boolean } {
  if (!entry.included) return { filters: [], preview: false };
  if (entry.source && !entry.refreshStaged) return { filters: entry.source.filters, preview: false };
  return { filters: entry.live ?? [], preview: true };
}

/** Whether a saved review has evidence to export: a scan, or a community snapshot with filters. */
export function hasExportEvidence(review: Pick<SavedReview, "memberships" | "community_sources">): boolean {
  return review.memberships.length > 0 || !!review.community_sources?.some((source) => source.filters.length > 0);
}
