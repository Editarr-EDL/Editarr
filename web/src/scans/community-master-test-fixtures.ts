import type { MasterFilter, MasterLink, MasterUpdate, MasterView, UpdateRow } from "../api/api-community-master-types";

/** A master filter row; `confidence` and `lowConfidence` set the master values. */
export function masterFilter(overrides: Partial<MasterFilter> & { confidence?: number; lowConfidence?: boolean } = {}): MasterFilter {
  const { confidence = 0.4, lowConfidence = false, ...rest } = overrides;
  const values = { startMs: 600_000, endMs: 605_050, subcategory: "punching", severity: "high", action: "skip" };
  return {
    filterId: "f1", category: "violence", local: null, effective: values, state: lowConfidence ? "low_confidence" : "pending",
    touched: false, locallyVerified: false, myVote: null, active: true,
    master: { ...values, spread: { startMs: 300, endMs: 350 }, confidence, contributorCount: 4,
      support: { attestations: 4, upvotes: 1 }, negative: { downvotes: 1, disputes: 0, coverageNegatives: 1 }, lowConfidence, present: true },
    ...rest,
  };
}

export function masterLink(overrides: Partial<MasterLink> = {}): MasterLink {
  return { linkId: "link-1", localVersionId: "mv_1", communityVersionId: "v_12ab", label: "Theatrical", status: "confirmed",
    unconfirmed: false, tier: "2", matchPercent: 80, appliedRevision: 12, latestRevision: 12, alignmentState: "not_required",
    alignment: null, pendingUpdate: null, ...overrides };
}

export function masterView(filters: MasterFilter[], link: Partial<MasterLink> = {}): MasterView {
  return { version: 1, link: masterLink(link), preferences: { confidenceThreshold: 0, showLowConfidence: false }, filters };
}

const values = (startMs: number) => ({ startMs, endMs: startMs + 5000, subcategory: "punching", severity: "high", action: "skip" });

export function updateRow(rowId: string, overrides: Partial<UpdateRow> = {}): UpdateRow {
  return { rowId, filterId: rowId, category: "violence", touched: false, changedFields: ["startMs"], mine: null,
    oldMaster: values(600_000), newMaster: values(601_000), mergedInto: null, allowed: ["accept", "dismiss"], canAccept: true, ...overrides };
}

export function masterUpdate(groups: Partial<MasterUpdate["groups"]>, overrides: Partial<MasterUpdate> = {}): MasterUpdate {
  return { version: 1, fromRevision: 12, toRevision: 13, alignmentRequired: false, alignmentApproved: false,
    groups: { new: [], changed: [], removed: [], nowLowConfidence: [], ...groups }, ...overrides };
}
