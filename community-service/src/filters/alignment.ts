import type { Cluster } from './clustering';
import type { PreparedEntry } from './counted';
import type { MergePolicy } from './policy';
import { compareAscii, floatMedian, integerMedian } from './stats';
import type { Fit } from './types';

export const IDENTITY_FIT: Readonly<Fit> = Object.freeze({ mode: 'identity', offsetMs: 0, scale: 1, anchors: 0 });
export const NO_FIT: Readonly<Fit> = Object.freeze({ mode: 'none', offsetMs: 0, scale: 1, anchors: 0 });

type AlignmentPolicy = Pick<MergePolicy, 'matchToleranceMs' | 'alignmentSearchMs' | 'alignmentMinAnchors' |
  'alignmentMinDriftSpanMs' | 'alignmentMaxScaleDeviation'>;

interface Pair { entry: PreparedEntry; cluster: Cluster; d: number }

/** `aligned(t) = floor(scale × t + offsetMs + 0.5)`, clamped at 0 (§6.3). */
export function alignTime(timeMs: number, fit: Pick<Fit, 'offsetMs' | 'scale'>) {
  return Math.max(0, Math.floor(fit.scale * timeMs + fit.offsetMs + 0.5));
}

/**
 * Fit one non-identity submission against the core set (§6.3): a robust offset
 * from candidate pairs, one-to-one anchors, then an optional Theil–Sen drift fit.
 * `entries` are the submission's weight-1 support entries; `alignmentHint` is never used.
 */
export function fitSubmission(entries: readonly PreparedEntry[], core: readonly Cluster[], policy: AlignmentPolicy): Fit {
  const N = policy.matchToleranceMs, pairs: Pair[] = [];
  const coreByCategory = new Map<string, Cluster[]>();
  for (const cluster of core) coreByCategory.set(cluster.category, [...(coreByCategory.get(cluster.category) ?? []), cluster]);
  for (const entry of entries) {
    const { startMs, endMs, category } = entry.entry;
    for (const cluster of coreByCategory.get(category) ?? []) {
      const clusterDuration = cluster.medianEnd - cluster.medianStart;
      if (Math.abs(cluster.medianStart - startMs) > policy.alignmentSearchMs) continue;
      if (Math.abs(clusterDuration - (endMs - startMs)) > Math.max(N, 0.1 * clusterDuration)) continue;
      pairs.push({ entry, cluster, d: cluster.medianStart - startMs });
    }
  }
  if (pairs.length === 0) return { ...NO_FIT };
  const sorted = pairs.map(pair => pair.d).sort((a, b) => a - b);
  let best = pairs[0].d, bestInliers = -1;
  for (const { d } of pairs) {
    const inliers = upperBound(sorted, d + N) - lowerBound(sorted, d - N);
    if (inliers > bestInliers || (inliers === bestInliers &&
        (Math.abs(d) < Math.abs(best) || (Math.abs(d) === Math.abs(best) && d < best)))) {
      best = d; bestInliers = inliers;
    }
  }
  const inlierPairs = pairs.filter(pair => Math.abs(pair.d - best) <= N);
  const offset = integerMedian(inlierPairs.map(pair => pair.d));
  const anchors = oneToOne(inlierPairs, offset);
  if (anchors.length === 0) return { ...NO_FIT };
  const offsetFit: Fit = { mode: 'offset', offsetMs: offset, scale: 1, anchors: anchors.length };
  const starts = anchors.map(pair => pair.entry.entry.startMs);
  if (anchors.length < policy.alignmentMinAnchors ||
      Math.max(...starts) - Math.min(...starts) < policy.alignmentMinDriftSpanMs) return offsetFit;
  const slopes: number[] = [];
  for (let i = 0; i < anchors.length; i++) {
    for (let j = i + 1; j < anchors.length; j++) {
      const de = anchors[j].entry.entry.startMs - anchors[i].entry.entry.startMs;
      if (de !== 0) slopes.push((anchors[j].cluster.medianStart - anchors[i].cluster.medianStart) / de);
    }
  }
  if (slopes.length === 0) return offsetFit;
  const scale = floatMedian(slopes);
  if (!(Math.abs(scale - 1) <= policy.alignmentMaxScaleDeviation)) return offsetFit;
  const driftOffset = Math.floor(floatMedian(anchors.map(pair => pair.cluster.medianStart - scale * pair.entry.entry.startMs)) + 0.5);
  const driftFit: Fit = { mode: 'drift', offsetMs: driftOffset, scale, anchors: anchors.length };
  return countInliers(anchors, driftFit, N) < countInliers(anchors, offsetFit, N) ? offsetFit : driftFit;
}

/** Keep one pair per entry and cluster: smallest |d − offset|, then cluster start, then entryId (§6.3 step 3). */
function oneToOne(pairs: readonly Pair[], offset: number) {
  const ordered = [...pairs].sort((a, b) =>
    Math.abs(a.d - offset) - Math.abs(b.d - offset) || a.cluster.medianStart - b.cluster.medianStart ||
    compareAscii(a.entry.entry.entryId, b.entry.entry.entryId) || a.cluster.medianEnd - b.cluster.medianEnd ||
    a.cluster.index - b.cluster.index);
  const usedEntries = new Set<PreparedEntry>(), usedClusters = new Set<Cluster>(), anchors: Pair[] = [];
  for (const pair of ordered) {
    if (usedEntries.has(pair.entry) || usedClusters.has(pair.cluster)) continue;
    usedEntries.add(pair.entry); usedClusters.add(pair.cluster); anchors.push(pair);
  }
  return anchors;
}

function countInliers(anchors: readonly Pair[], fit: Fit, toleranceMs: number) {
  return anchors.filter(({ entry, cluster }) =>
    Math.abs(alignTime(entry.entry.startMs, fit) - cluster.medianStart) <= toleranceMs &&
    Math.abs(alignTime(entry.entry.endMs, fit) - cluster.medianEnd) <= toleranceMs).length;
}

function lowerBound(sorted: readonly number[], value: number) {
  let low = 0, high = sorted.length;
  while (low < high) { const middle = (low + high) >> 1; if (sorted[middle] < value) low = middle + 1; else high = middle; }
  return low;
}

function upperBound(sorted: readonly number[], value: number) {
  let low = 0, high = sorted.length;
  while (low < high) { const middle = (low + high) >> 1; if (sorted[middle] <= value) low = middle + 1; else high = middle; }
  return low;
}
