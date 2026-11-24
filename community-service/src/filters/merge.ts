import { alignTime, fitSubmission, IDENTITY_FIT, NO_FIT } from './alignment';
import { classifyCluster } from './classification';
import { sweepClusters } from './clustering';
import { selectCounted } from './counted';
import { assignFilterIds } from './ids';
import { DEFAULT_MERGE_POLICY, type MergePolicy } from './policy';
import { nextRevision, servedContent } from './revision';
import { collectSignals, type FilterBounds } from './signals';
import { compareAscii, integerMedian, wilson } from './stats';
import type { MasterFilter, MergeInput, MergeResult } from './types';

/**
 * Recompute a version's master set from its stored inputs (§6). The result is a
 * pure function of `input` and `policy`; input order never affects it.
 */
export function mergeVersion(input: MergeInput, policy: MergePolicy = DEFAULT_MERGE_POLICY): MergeResult {
  const N = policy.matchToleranceMs, { moderation } = input;
  const { counted, states } = selectCounted(input.submissions, input.referenceKeys, moderation);
  const rank = new Map(input.registry.map(row => [row.filterId, row.createdSeq]));
  const supports = (submission: (typeof counted)[number]) => submission.entries.filter(entry => entry.weight === 1);

  const identity = counted.filter(submission => submission.identity);
  const core = sweepClusters(identity.flatMap(supports), N, rank).clusters;
  for (const submission of counted) {
    submission.fit = submission.identity ? { ...IDENTITY_FIT }
      : core.length === 0 ? { ...NO_FIT } : fitSubmission(supports(submission), core, policy);
    for (const entry of [...submission.entries, ...submission.blockers]) {
      entry.alignedStartMs = alignTime(entry.entry.startMs, submission.fit);
      entry.alignedEndMs = alignTime(entry.entry.endMs, submission.fit);
    }
  }

  const { clusters, duplicates } = sweepClusters(counted.flatMap(supports), N, rank);
  const { ids, rows, resolve, mergedIds } = assignFilterIds(input.versionId, clusters, duplicates, input.registry, input.nextCreatedSeq);
  const classified = clusters.map(cluster => {
    const filterId = ids.get(cluster)!, row = rows.get(filterId)!;
    const classification = classifyCluster(cluster, row, policy);
    row.incumbentSubcategory = classification.subcategory;
    row.incumbentAction = classification.action;
    return { filterId, cluster, classification, quarantined: row.quarantined };
  });
  const bounds: FilterBounds[] = classified.map(({ filterId, cluster, classification }) =>
    ({ filterId, cluster, category: classification.category, startMs: classification.startMs, endMs: classification.endMs }));
  const quarantinedContributors = new Set(moderation.quarantinedContributors);
  const { counts, rows: attestations } = collectSignals({
    filters: bounds, counted, duplicates, idOf: cluster => ids.get(cluster)!, resolve, votes: input.votes,
    quarantinedContributors, toleranceMs: N,
  });

  const filters: MasterFilter[] = classified.map(({ filterId, classification, quarantined }) => {
    const count = counts.get(filterId)!;
    const positive = count.attestations + count.upvotes;
    const negative = count.downvotes + count.disputes + policy.coverageNegativeWeight * count.coverageNegatives;
    const confidence = wilson(positive, negative);
    const { category, subcategory, severity, action, startMs, endMs, spread, tallies } = classification;
    return {
      filterId, mergedIds: mergedIds.get(filterId) ?? [], category, subcategory, severity, action, startMs, endMs, spread,
      confidence, contributorCount: count.attestations,
      support: { attestations: count.attestations, upvotes: count.upvotes },
      negative: { downvotes: count.downvotes, disputes: count.disputes, coverageNegatives: count.coverageNegatives },
      lowConfidence: negative > positive,
      served: confidence >= policy.confidenceFloor && !quarantined && !moderation.versionQuarantined,
      quarantined, tallies,
    };
  }).sort(compareSnapshot);
  capServed(filters, policy.maxServedFilters);
  const servedFilters = filters.filter(filter => filter.served);

  const runtimeMs = counted.length ? integerMedian(counted.map(submission => submission.input.durationMs)) : null;
  const trust = versionTrust(input, counted.map(submission => submission.input.contributorId), rows, quarantinedContributors);
  const computed = servedContent(input.versionId, input.header, runtimeMs, trust, servedFilters);
  const frozen = moderation.versionQuarantined ? input.previous?.servedContent ?? null : null;
  const content = frozen ?? computed;
  const fits = new Map(counted.map(submission => [submission.input.submissionId, submission.fit]));
  return {
    versionId: input.versionId, filters, servedFilters,
    registry: [...rows.values()].sort((a, b) => compareAscii(a.filterId, b.filterId)),
    submissions: states.map(state => ({ ...state, fit: fits.get(state.submissionId) ?? null })),
    attestations, runtimeMs, trust, servedContent: content,
    revision: nextRevision(input.previous, content, moderation.versionQuarantined),
  };
}

/** Snapshot order (§7.3): start, end, category, filterId. */
export function compareSnapshot(a: MasterFilter, b: MasterFilter) {
  return a.startMs - b.startMs || a.endMs - b.endMs || compareAscii(a.category, b.category) || compareAscii(a.filterId, b.filterId);
}

/** Keep at most `limit` served filters: highest confidence first, then snapshot order. */
function capServed(filters: MasterFilter[], limit: number) {
  const served = filters.filter(filter => filter.served);
  if (served.length <= limit) return;
  const ranked = [...served].sort((a, b) => b.confidence - a.confidence || compareSnapshot(a, b));
  for (const filter of ranked.slice(limit)) filter.served = false;
}

/**
 * Version trust (§3.5): positives are contributors with a counted submission or an
 * upvote on a version filter; negatives are the others with only downvotes.
 */
function versionTrust(input: MergeInput, countedContributors: readonly string[], rows: ReadonlyMap<string, unknown>,
  quarantined: ReadonlySet<string>) {
  const positives = new Set(countedContributors), downvoters = new Set<string>();
  for (const vote of input.votes) {
    if (vote.excluded || quarantined.has(vote.contributorId) || !rows.has(vote.filterId)) continue;
    (vote.vote === 'up' ? positives : downvoters).add(vote.contributorId);
  }
  const negatives = [...downvoters].filter(contributor => !positives.has(contributor)).length;
  return wilson(positives.size, negatives);
}
