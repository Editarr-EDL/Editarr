import { bestEvidenceTier, type EvidenceTier, type StoredKey } from './keys';
import { labelsEqual, type Label } from './labels';

export type Disposition = 'attached' | 'created_confirmed' | 'created_candidate';
export interface ExistingVersion {
  versionId: string; status: 'confirmed' | 'candidate' | 'merged_into'; trust: number; label: Label; keys: StoredKey[];
}
export interface VersionDecisionInput { choice: 'alias' | 'other'; label: Label; keys: StoredKey[] }
export type VersionDecision =
  | { disposition: 'attached'; versionId: string; tier: EvidenceTier }
  | { disposition: 'created_confirmed' | 'created_candidate' };

const ascii = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

/**
 * D13 version acceptance (§3.5 steps 3–6) as a pure function over the work's versions.
 * Attaches to the best equal-label tier 1–3 match (lowest tier, confirmed, trust, then ID);
 * otherwise an alias with no equal-label version creates a confirmed version, and everything
 * else creates a candidate.
 */
export function decideVersion(input: VersionDecisionInput, versions: readonly ExistingVersion[]): VersionDecision {
  const sameLabel = versions.filter(version => version.status !== 'merged_into' && labelsEqual(version.label, input.label));
  const matches = sameLabel.flatMap(version => {
    const tier = bestEvidenceTier(input.keys, version.keys);
    return tier === null ? [] : [{ version, tier }];
  }).sort((a, b) => a.tier - b.tier ||
    Number(a.version.status !== 'confirmed') - Number(b.version.status !== 'confirmed') ||
    b.version.trust - a.version.trust || ascii(a.version.versionId, b.version.versionId));
  if (matches.length) return { disposition: 'attached', versionId: matches[0].version.versionId, tier: matches[0].tier };
  if (input.choice === 'alias' && !sameLabel.length) return { disposition: 'created_confirmed' };
  return { disposition: 'created_candidate' };
}

/** One row of `version_acceptances`, the bookkeeping behind counted inputs, runtime and promotion. */
export interface AcceptanceRow {
  submissionId?: string; contributorId: string; acceptedSeq: number; tier: EvidenceTier | null; durationMs: number | null; quarantined: boolean;
}

/** Counted rows (§6.1): the latest unquarantined acceptance per contributor. */
export function countedAcceptances(rows: readonly AcceptanceRow[]): AcceptanceRow[] {
  const latest = new Map<string, AcceptanceRow>();
  for (const row of rows) {
    if (row.quarantined) continue;
    const current = latest.get(row.contributorId);
    if (!current || row.acceptedSeq > current.acceptedSeq) latest.set(row.contributorId, row);
  }
  return [...latest.values()];
}

/** Integer median (§6.4); the floor of the mean of the two middle values for even counts. */
export function integerMedian(values: readonly number[]): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : Math.floor((sorted[middle - 1] + sorted[middle]) / 2);
}

/** Wilson lower bound with one positive and one negative prior, rounded as §6.8 specifies. */
export function wilson(positive: number, negative: number): number {
  if (![positive, negative].every(value => Number.isFinite(value) && value >= 0)) throw new Error('invalid_wilson_counts');
  const n = positive + negative + 2, p = (positive + 1) / n, z = 1.96, z2 = z * z;
  const lower = (p + z2 / (2 * n) - z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / (1 + z2 / n);
  return Math.floor(Math.max(0, Math.min(1, lower)) * 1e9 + 0.5) / 1e9;
}

/**
 * Version trust (§3.5): positives are contributors with a counted submission or an upvote;
 * negatives are other contributors with only downvotes or disputes on the version's filters.
 */
export function versionTrust(positives: Iterable<string>, negatives: Iterable<string> = []): number {
  const positive = new Set(positives);
  const negative = new Set([...negatives].filter(id => !positive.has(id)));
  return wilson(positive.size, negative.size);
}

export interface DerivedVersionState { runtimeMs: number | null; evidenceContributors: number; trust: number; promote: boolean }

/**
 * Derive runtime, promotion and acceptance-based trust from a version's acceptance rows.
 * A candidate is promoted when `promotionContributors` distinct contributors have a counted
 * acceptance attached (or founded) with tier 1–3 evidence.
 */
export function deriveVersionState(rows: readonly AcceptanceRow[], status: string,
  promotionContributors: number): DerivedVersionState {
  const counted = countedAcceptances(rows);
  const evidenceContributors = new Set(counted.filter(row => row.tier !== null).map(row => row.contributorId)).size;
  return { runtimeMs: integerMedian(counted.flatMap(row => row.durationMs === null ? [] : [row.durationMs])), evidenceContributors,
    trust: versionTrust(counted.map(row => row.contributorId)),
    promote: status === 'candidate' && evidenceContributors >= promotionContributors };
}
