import type { MasterFilter } from './types';

/** A served filter in snapshot key order (§7.3). */
export function snapshotFilter(filter: MasterFilter) {
  return {
    filterId: filter.filterId, mergedIds: [...filter.mergedIds], category: filter.category,
    subcategory: filter.subcategory, severity: filter.severity, action: filter.action,
    startMs: filter.startMs, endMs: filter.endMs, spread: { startMs: filter.spread.startMs, endMs: filter.spread.endMs },
    confidence: filter.confidence, contributorCount: filter.contributorCount,
    support: { attestations: filter.support.attestations, upvotes: filter.support.upvotes },
    negative: { downvotes: filter.negative.downvotes, disputes: filter.negative.disputes,
      coverageNegatives: filter.negative.coverageNegatives },
    lowConfidence: filter.lowConfidence,
  };
}

/**
 * Canonical merge-derived served content: version runtime, trust and the served
 * filters in snapshot order. Work, label and status are outside the merge engine;
 * callers fold those into `nextRevision` when they build the full snapshot content.
 */
export function servedContent(runtimeMs: number | null, trust: number, servedFilters: readonly MasterFilter[]) {
  return JSON.stringify({ runtimeMs, trust, filters: servedFilters.map(snapshotFilter) });
}

/**
 * Advance the revision only when served content changed (§6.9). A version without
 * a stored snapshot keeps its creation revision (default 1) and reports a change.
 */
export function nextRevision(previous: { revision: number; servedContent: string | null } | null | undefined, content: string) {
  if (!previous) return { revision: 1, changed: true };
  if (previous.servedContent === null) return { revision: previous.revision, changed: true };
  if (previous.servedContent === content) return { revision: previous.revision, changed: false };
  return { revision: previous.revision + 1, changed: true };
}
