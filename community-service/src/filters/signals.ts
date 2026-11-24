import type { Cluster } from './clustering';
import { withinTolerance } from './clustering';
import type { PreparedEntry, PreparedSubmission } from './counted';
import { alignTime } from './alignment';
import { compareAscii } from './stats';
import type { AttestationResult, VoteInput } from './types';

/** A final filter's identity and bounds, as needed for signal assignment. */
export interface FilterBounds {
  filterId: string;
  category: string;
  startMs: number;
  endMs: number;
  cluster: Cluster;
}

export interface SignalCounts {
  attestations: number;
  upvotes: number;
  downvotes: number;
  disputes: number;
  coverageNegatives: number;
}

interface SignalContext {
  filters: readonly FilterBounds[];
  counted: readonly PreparedSubmission[];
  duplicates: ReadonlyMap<PreparedEntry, Cluster>;
  idOf: (cluster: Cluster) => string;
  /** Resolve a filter or retired ID to a current filter ID through `mergedInto`. */
  resolve: (id: string | null) => string | null;
  votes: readonly VoteInput[];
  quarantinedContributors: ReadonlySet<string>;
  toleranceMs: number;
}

const PRECEDENCE = { support: 0, dispute: 1, coverage_negative: 2 } as const;

/**
 * Assign disputes, coverage negatives and votes, and keep at most one signal per
 * contributor and filter by precedence (§6.8). Returns per-filter counts and all
 * attestation rows, including implicit coverage negatives.
 */
export function collectSignals(context: SignalContext) {
  const byId = new Map(context.filters.map(filter => [filter.filterId, filter]));
  const finder = filterFinder(context.filters, context.toleranceMs);
  const memberOf = new Map<PreparedEntry, string>();
  for (const filter of context.filters) for (const member of filter.cluster.members) memberOf.set(member, filter.filterId);
  const rows: AttestationResult[] = [];
  /** filterId → contributorId → the best attestation row so far. */
  const best = new Map<string, Map<string, AttestationResult>>();
  /** filterId → contributors with any cue on that filter, which blocks implicit negatives. */
  const touched = new Map<string, Set<string>>();
  const touch = (filterId: string, contributorId: string) =>
    touched.set(filterId, (touched.get(filterId) ?? new Set()).add(contributorId));
  for (const submission of context.counted) {
    const contributorId = submission.input.contributorId;
    for (const prepared of submission.entries) {
      const { entry } = prepared;
      const row: AttestationResult = {
        submissionId: submission.input.submissionId, entryId: entry.entryId, contributorId, kind: entry.kind,
        implicit: false, filterId: null, alignedStartMs: prepared.alignedStartMs, alignedEndMs: prepared.alignedEndMs,
        weight: prepared.weight, ignoredReason: prepared.ignoredReason, counted: false,
      };
      rows.push(row);
      const duplicate = context.duplicates.get(prepared);
      if (duplicate) { row.weight = 0; row.ignoredReason = 'duplicate'; row.filterId = context.idOf(duplicate); }
      else if (prepared.weight === 1) row.filterId = memberOf.get(prepared) ?? null;
      else {
        const proposed = context.resolve(entry.proposedFilterId);
        row.filterId = proposed !== null && byId.get(proposed)?.category === entry.category ? proposed
          : finder(entry.category, prepared.alignedStartMs, prepared.alignedEndMs);
      }
      if (row.filterId === null) continue;
      touch(row.filterId, contributorId);
      if (row.weight === 0 && entry.kind === 'support') continue;
      const perFilter = best.get(row.filterId) ?? new Map<string, AttestationResult>();
      const current = perFilter.get(contributorId);
      if (!current || PRECEDENCE[row.kind] < PRECEDENCE[current.kind] ||
          (row.kind === current.kind && compareAscii(row.entryId, current.entryId) < 0)) perFilter.set(contributorId, row);
      best.set(row.filterId, perFilter);
    }
  }
  for (const perFilter of best.values()) for (const row of perFilter.values()) row.counted = true;
  const counts = new Map<string, SignalCounts>();
  for (const filter of context.filters) {
    const perFilter = best.get(filter.filterId) ?? new Map<string, AttestationResult>();
    const blocked = touched.get(filter.filterId) ?? new Set<string>();
    for (const submission of context.counted) {
      const contributorId = submission.input.contributorId;
      if (perFilter.has(contributorId) || blocked.has(contributorId) || !covers(submission, filter)) continue;
      const row: AttestationResult = {
        submissionId: submission.input.submissionId, entryId: `cov:${filter.filterId}`, contributorId,
        kind: 'coverage_negative', implicit: true, filterId: filter.filterId, alignedStartMs: null, alignedEndMs: null,
        weight: 0, ignoredReason: null, counted: true,
      };
      rows.push(row); perFilter.set(contributorId, row);
    }
    const signals = [...perFilter.values()];
    counts.set(filter.filterId, {
      attestations: signals.filter(row => row.kind === 'support').length, upvotes: 0, downvotes: 0,
      disputes: signals.filter(row => row.kind === 'dispute').length,
      coverageNegatives: signals.filter(row => row.kind === 'coverage_negative').length,
    });
    best.set(filter.filterId, perFilter);
  }
  countVotes(context, best, counts);
  rows.sort((a, b) => compareAscii(a.submissionId, b.submissionId) || compareAscii(a.entryId, b.entryId));
  return { counts, rows };
}

/** Count each contributor's vote once per filter when it has no attestation signal there. */
function countVotes(context: SignalContext, signals: ReadonlyMap<string, ReadonlyMap<string, AttestationResult>>,
  counts: Map<string, SignalCounts>) {
  const chosen = new Map<string, VoteInput>();
  for (const vote of context.votes) {
    if (vote.excluded || context.quarantinedContributors.has(vote.contributorId)) continue;
    const target = context.resolve(vote.filterId);
    if (target === null) continue;
    const key = JSON.stringify([target, vote.contributorId]), current = chosen.get(key);
    const rank = (candidate: VoteInput) => candidate.filterId === target ? '' : candidate.filterId;
    if (!current || compareAscii(rank(vote), rank(current)) < 0 ||
        (rank(vote) === rank(current) && compareAscii(vote.vote, current.vote) < 0)) chosen.set(key, vote);
  }
  for (const [key, vote] of chosen) {
    const [target] = JSON.parse(key) as [string, string];
    if (signals.get(target)?.has(vote.contributorId)) continue;
    const count = counts.get(target)!;
    if (vote.vote === 'up') count.upvotes++; else count.downvotes++;
  }
}

/** Return a finder for the closest same-category filter within ±N (§6.5 step 1), ties by smaller filterId. */
function filterFinder(filters: readonly FilterBounds[], toleranceMs: number) {
  const byCategory = new Map<string, FilterBounds[]>();
  for (const filter of filters) byCategory.set(filter.category, [...(byCategory.get(filter.category) ?? []), filter]);
  for (const list of byCategory.values()) list.sort((a, b) => a.startMs - b.startMs);
  return (category: string, startMs: number, endMs: number) => {
    const list = byCategory.get(category) ?? [];
    let low = 0, high = list.length;
    while (low < high) { const middle = (low + high) >> 1; if (list[middle].startMs < startMs - toleranceMs) low = middle + 1; else high = middle; }
    let found: FilterBounds | null = null, foundMax = 0, foundSum = 0;
    for (let i = low; i < list.length && list[i].startMs <= startMs + toleranceMs; i++) {
      const filter = list[i];
      if (!withinTolerance(filter.startMs, filter.endMs, startMs, endMs, toleranceMs)) continue;
      const ds = Math.abs(filter.startMs - startMs), de = Math.abs(filter.endMs - endMs);
      const max = Math.max(ds, de), sum = ds + de;
      if (!found || max < foundMax || (max === foundMax && (sum < foundSum ||
          (sum === foundSum && compareAscii(filter.filterId, found.filterId) < 0)))) {
        found = filter; foundMax = max; foundSum = sum;
      }
    }
    return found?.filterId ?? null;
  };
}

/** True when the filter lies inside one aligned coverage interval of its category. */
function covers(submission: PreparedSubmission, filter: FilterBounds) {
  const coverage = submission.input.coverage.find(item => item.category === filter.category);
  if (!coverage) return false;
  const intervals = coverage.intervals;
  let low = 0, high = intervals.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (alignTime(intervals[middle].startMs, submission.fit) <= filter.startMs) low = middle + 1; else high = middle;
  }
  return low > 0 && alignTime(intervals[low - 1].endMs, submission.fit) >= filter.endMs;
}
