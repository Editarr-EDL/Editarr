import { compareCanonical, type PreparedEntry } from './counted';
import { compareAscii, insertSorted, sortedIntegerMedian } from './stats';

/** A running cluster from the same-filter sweep (§6.5). */
export interface Cluster {
  /** Creation order within the sweep. */
  index: number;
  category: string;
  starts: number[];
  ends: number[];
  medianStart: number;
  medianEnd: number;
  /** Weight-1 members in canonical order. */
  members: PreparedEntry[];
  contributors: Set<string>;
  priorCounts: Map<string, number>;
}

/** Registry `createdSeq` by filter ID; IDs absent here are not registry IDs. */
export type PriorRank = ReadonlyMap<string, number>;

/** The plurality member prior ID among `allowed` IDs, ties by smaller `createdSeq` then ID (§6.6 step 2). */
export function pluralityPrior(counts: ReadonlyMap<string, number>, rank: PriorRank, allowed = (_: string) => true) {
  let best: string | null = null, bestCount = 0;
  for (const [id, count] of counts) {
    if (!rank.has(id) || !allowed(id)) continue;
    if (best === null || count > bestCount ||
        (count === bestCount && (rank.get(id)! - rank.get(best)! || compareAscii(id, best)) < 0)) {
      best = id; bestCount = count;
    }
  }
  return best;
}

/** True when bounds are within ±N (inclusive) of a cluster's medians (§6.5 step 1). */
export function withinTolerance(medianStart: number, medianEnd: number, startMs: number, endMs: number, toleranceMs: number) {
  return Math.abs(medianStart - startMs) <= toleranceMs && Math.abs(medianEnd - endMs) <= toleranceMs;
}

/**
 * Sweep weight-1 support entries in canonical order into clusters (§6.5).
 * Returns the clusters in creation order and, for each repeat-contributor entry,
 * the cluster it duplicated.
 */
export function sweepClusters(entries: readonly PreparedEntry[], toleranceMs: number, rank: PriorRank) {
  const ordered = [...entries].sort(compareCanonical);
  const clusters: Cluster[] = [], duplicates = new Map<PreparedEntry, Cluster>();
  const live = new Map<string, Cluster[]>();
  for (const entry of ordered) {
    const { alignedStartMs: start, alignedEndMs: end } = entry, category = entry.entry.category;
    // Member starts never exceed the current start, so running median starts only grow; prune stale clusters.
    const window = (live.get(category) ?? []).filter(cluster => cluster.medianStart >= start - toleranceMs);
    live.set(category, window);
    let chosen: Cluster | null = null;
    for (const cluster of window) {
      if (!withinTolerance(cluster.medianStart, cluster.medianEnd, start, end, toleranceMs)) continue;
      if (!chosen || compareCandidates(cluster, chosen, entry, rank) < 0) chosen = cluster;
    }
    const contributorId = entry.submission.input.contributorId;
    if (chosen?.contributors.has(contributorId)) { duplicates.set(entry, chosen); continue; }
    if (!chosen) {
      chosen = { index: clusters.length, category, starts: [], ends: [], medianStart: start, medianEnd: end,
        members: [], contributors: new Set(), priorCounts: new Map() };
      clusters.push(chosen); window.push(chosen);
    }
    chosen.members.push(entry);
    chosen.contributors.add(contributorId);
    insertSorted(chosen.starts, start); insertSorted(chosen.ends, end);
    chosen.medianStart = sortedIntegerMedian(chosen.starts);
    chosen.medianEnd = sortedIntegerMedian(chosen.ends);
    const prior = entry.entry.priorFilterId;
    if (prior !== null) chosen.priorCounts.set(prior, (chosen.priorCounts.get(prior) ?? 0) + 1);
  }
  return { clusters, duplicates };
}

/** Candidate preference (§6.5 step 2): smaller max delta, smaller delta sum, proposed prior ID, earlier cluster. */
function compareCandidates(a: Cluster, b: Cluster, entry: PreparedEntry, rank: PriorRank) {
  const deltas = (cluster: Cluster) => {
    const ds = Math.abs(cluster.medianStart - entry.alignedStartMs), de = Math.abs(cluster.medianEnd - entry.alignedEndMs);
    return [Math.max(ds, de), ds + de] as const;
  };
  const [maxA, sumA] = deltas(a), [maxB, sumB] = deltas(b);
  if (maxA !== maxB) return maxA - maxB;
  if (sumA !== sumB) return sumA - sumB;
  const proposed = entry.entry.proposedFilterId;
  if (proposed !== null) {
    const matchA = pluralityPrior(a.priorCounts, rank) === proposed, matchB = pluralityPrior(b.priorCounts, rank) === proposed;
    if (matchA !== matchB) return matchA ? -1 : 1;
  }
  return a.index - b.index;
}
