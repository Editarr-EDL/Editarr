import type { MasterFilter, MasterLink, MasterView } from "../api/api-community-master-types";

/** A master filter row; `confidence` and `lowConfidence` set the master values. */
export function masterFilter(overrides: Partial<MasterFilter> & { confidence?: number; lowConfidence?: boolean } = {}): MasterFilter {
  const { confidence = 0.4, lowConfidence = false, ...rest } = overrides;
  const values = { startMs: 600_000, endMs: 605_050, subcategory: "punching", severity: "high", action: "skip" };
  return {
    filterId: "f1", category: "violence", locallyVerified: false, myVote: null, active: true,
    master: { ...values, spread: { startMs: 300, endMs: 350 }, confidence, contributorCount: 4,
      support: { attestations: 4, upvotes: 1 }, negative: { downvotes: 1, disputes: 0, coverageNegatives: 1 }, lowConfidence, present: true },
    ...rest,
  };
}

export function masterLink(overrides: Partial<MasterLink> = {}): MasterLink {
  return { linkId: "link-1", localVersionId: "mv_1", communityVersionId: "v_12ab", label: "Theatrical", status: "confirmed",
    unconfirmed: false, tier: "2", matchPercent: 80, appliedRevision: 12, latestRevision: 12, alignmentState: "not_required",
    aliasId: "theatrical", alignment: null, alignmentRevision: null, linkState: "active", mergedInto: null, ...overrides };
}

export function masterView(filters: MasterFilter[], link: Partial<MasterLink> = {}): MasterView {
  return { version: 1, link: masterLink(link), preferences: { confidenceThreshold: 0, showLowConfidence: false }, filters };
}
