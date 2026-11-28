import { alignTime, fitSubmission, IDENTITY_FIT, NO_FIT } from './alignment';
import { classifyCluster } from './classification';
import { sweepClusters } from './clustering';
import { selectCounted } from './counted';
import { assignFilterIds } from './ids';
import { DEFAULT_MERGE_POLICY, type MergePolicy } from './policy';
import { nextRevision, servedContent, SNAPSHOT_MAX_BYTES, snapshotFilter } from './revision';
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
    row.incumbentExpression = classification.expression;
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
    const { category, subcategory, severity, action, expression, startMs, endMs, spread, tallies } = classification;
    return {
      filterId, mergedIds: mergedIds.get(filterId) ?? [], category, subcategory, severity, action, expression, startMs, endMs, spread,
      confidence, contributorCount: count.attestations,
      support: { attestations: count.attestations, upvotes: count.upvotes },
      negative: { downvotes: count.downvotes, disputes: count.disputes, coverageNegatives: count.coverageNegatives },
      lowConfidence: negative > positive,
      served: confidence >= policy.confidenceFloor && !quarantined && !moderation.versionQuarantined,
      quarantined, tallies,
    };
  }).sort(compareSnapshot);
  capServed(filters, policy.maxServedFilters);

  const durations = counted.flatMap(({ input }) => input.durationMs === null ? [] : [input.durationMs]);
  const runtimeMs = durations.length ? integerMedian(durations) : null;
  const trust = versionTrust(input, counted.map(submission => submission.input.contributorId), rows, quarantinedContributors);
  const content = (served: readonly MasterFilter[]) => servedContent(input.versionId, input.header, runtimeMs, trust, served);
  const computed = fitServedSize(filters, content);
  const servedFilters = filters.filter(filter => filter.served);
  const frozen = moderation.versionQuarantined ? input.previous?.servedContent ?? null : null;
  const stored = frozen ?? computed;
  const fits = new Map(counted.map(submission => [submission.input.submissionId, submission.fit]));
  return {
    versionId: input.versionId, filters, servedFilters,
    registry: [...rows.values()].sort((a, b) => compareAscii(a.filterId, b.filterId)),
    submissions: states.map(state => ({ ...state, fit: fits.get(state.submissionId) ?? null })),
    attestations, runtimeMs, trust, servedContent: stored,
    revision: nextRevision(input.previous, stored, moderation.versionQuarantined),
  };
}

/** Snapshot order (§7.3): start, end, category, filterId. */
export function compareSnapshot(a: MasterFilter, b: MasterFilter) {
  return a.startMs - b.startMs || a.endMs - b.endMs || compareAscii(a.category, b.category) || compareAscii(a.filterId, b.filterId);
}

/** Served filters by serving priority: highest confidence first, then snapshot order. */
function servingRank(filters: readonly MasterFilter[]) {
  return filters.filter(filter => filter.served).sort((a, b) => b.confidence - a.confidence || compareSnapshot(a, b));
}

/** Keep at most `limit` served filters, in serving priority. */
function capServed(filters: MasterFilter[], limit: number) {
  for (const filter of servingRank(filters).slice(limit)) filter.served = false;
}

/** Room for the `revision` and `generatedAt` fields `snapshotBytes` inserts, at their largest. */
const REVISION_FIELDS_BYTES = new TextEncoder().encode(
  `,"revision":${Number.MAX_SAFE_INTEGER},"generatedAt":${Number.MAX_SAFE_INTEGER}`).byteLength;
const utf8Length = (value: string) => new TextEncoder().encode(value).byteLength;

/**
 * Keep the snapshot within `SNAPSHOT_MAX_BYTES` (§7.3): while its bytes, with the largest
 * possible revision fields, would exceed it, stop serving the lowest-priority served filter
 * (the count cap's order). Returns the served content of the filters left served.
 */
function fitServedSize(filters: MasterFilter[], content: (served: readonly MasterFilter[]) => string) {
  const served = () => filters.filter(filter => filter.served);
  let result = content(served());
  let excess = utf8Length(result) + REVISION_FIELDS_BYTES - SNAPSHOT_MAX_BYTES;
  if (excess <= 0) return result;
  // Each dropped filter frees its own JSON and one separating comma.
  for (const filter of servingRank(filters).reverse()) {
    if (excess <= 0) break;
    filter.served = false;
    excess -= utf8Length(JSON.stringify(snapshotFilter(filter))) + 1;
  }
  result = content(served());
  return result;
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
