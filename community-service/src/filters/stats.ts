/** Deterministic numeric helpers for the merge engine (§6.3, §6.4, §6.8). */

/** Compare strings by code unit, which is ASCII order for tokens. */
export function compareAscii(a: string, b: string) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Integer median (§6.4): the middle value, or `floor((a + b) / 2)` of the two middle values. */
export function integerMedian(values: readonly number[]) {
  if (values.length === 0) throw new Error('median_of_empty');
  const sorted = [...values].sort((a, b) => a - b), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : Math.floor((sorted[middle - 1] + sorted[middle]) / 2);
}

/** Float64 median (§6.3): an even count takes the mean of the two middle values. */
export function floatMedian(values: readonly number[]) {
  if (values.length === 0) throw new Error('median_of_empty');
  const sorted = Float64Array.from(values).sort(), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** Integer median of an already ascending array. */
export function sortedIntegerMedian(sorted: readonly number[]) {
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : Math.floor((sorted[middle - 1] + sorted[middle]) / 2);
}

/** Insert into an ascending array, keeping it sorted. */
export function insertSorted(sorted: number[], value: number) {
  let low = 0, high = sorted.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (sorted[middle] <= value) low = middle + 1; else high = middle;
  }
  sorted.splice(low, 0, value);
}

/** Largest absolute deviation from the median; 0 for one value (§6.4). */
export function spreadOf(values: readonly number[], median: number) {
  return values.reduce((max, value) => Math.max(max, Math.abs(value - median)), 0);
}

/**
 * Wilson lower bound with one positive and one negative prior and z = 1.96,
 * clamped to 0..1 and rounded to nine decimals (§6.8). `negative` may be fractional.
 */
export function wilson(positive: number, negative: number) {
  if (!(positive >= 0 && negative >= 0 && Number.isFinite(positive + negative))) throw new Error('invalid_wilson_counts');
  const z = 1.96, n = positive + negative + 2, p = (positive + 1) / n, zz = z * z;
  const lower = (p + zz / (2 * n) - z * Math.sqrt(p * (1 - p) / n + zz / (4 * n * n))) / (1 + zz / n);
  return Math.floor(Math.max(0, Math.min(1, lower)) * 1e9 + 0.5) / 1e9;
}
