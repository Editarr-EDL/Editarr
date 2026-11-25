import { z } from 'zod';
import type { Severity } from './types';

/** `MASTER_POLICY` operator configuration (contract §8.1). Every key is required. */
export interface MasterPolicy {
  matchToleranceMs: number;
  severityWeights: Readonly<Record<Severity, number>>;
  confidenceFloor: number;
  coverageNegativeWeight: number;
  candidatePromotionContributors: number;
  alignmentSearchMs: number;
  alignmentMinAnchors: number;
  alignmentMinDriftSpanMs: number;
  alignmentMaxScaleDeviation: number;
  maxServedFilters: number;
  syncRunsPerDay: number;
  syncMaxBatchesPerRun: number;
  syncRunWindowSeconds: number;
  versionChecksPerHour: number;
  submissionsPerDay: number;
}

/** The `MASTER_POLICY` keys that the merge engine (§6) reads. */
export type MergePolicy = Pick<MasterPolicy,
  'matchToleranceMs' | 'severityWeights' | 'confidenceFloor' | 'coverageNegativeWeight' |
  'alignmentSearchMs' | 'alignmentMinAnchors' | 'alignmentMinDriftSpanMs' |
  'alignmentMaxScaleDeviation' | 'maxServedFilters'>;

export const DEFAULT_MASTER_POLICY: Readonly<MasterPolicy> = Object.freeze({
  matchToleranceMs: 500,
  severityWeights: Object.freeze({ low: 1, medium: 1, high: 3 }),
  confidenceFloor: 0.05,
  coverageNegativeWeight: 1,
  candidatePromotionContributors: 3,
  alignmentSearchMs: 30_000,
  alignmentMinAnchors: 3,
  alignmentMinDriftSpanMs: 300_000,
  alignmentMaxScaleDeviation: 0.05,
  maxServedFilters: 4000,
  syncRunsPerDay: 1,
  syncMaxBatchesPerRun: 20,
  syncRunWindowSeconds: 600,
  versionChecksPerHour: 60,
  submissionsPerDay: 50,
});

export const DEFAULT_MERGE_POLICY: Readonly<MergePolicy> = Object.freeze({
  matchToleranceMs: DEFAULT_MASTER_POLICY.matchToleranceMs,
  severityWeights: DEFAULT_MASTER_POLICY.severityWeights,
  confidenceFloor: DEFAULT_MASTER_POLICY.confidenceFloor,
  coverageNegativeWeight: DEFAULT_MASTER_POLICY.coverageNegativeWeight,
  alignmentSearchMs: DEFAULT_MASTER_POLICY.alignmentSearchMs,
  alignmentMinAnchors: DEFAULT_MASTER_POLICY.alignmentMinAnchors,
  alignmentMinDriftSpanMs: DEFAULT_MASTER_POLICY.alignmentMinDriftSpanMs,
  alignmentMaxScaleDeviation: DEFAULT_MASTER_POLICY.alignmentMaxScaleDeviation,
  maxServedFilters: DEFAULT_MASTER_POLICY.maxServedFilters,
});

const count = (min: number, max: number) => z.number().int().min(min).max(max);
const weight = z.number().finite().min(0).max(1000);

/**
 * Strict `MASTER_POLICY` schema: every §8.1 key is required and unknown keys are
 * rejected. Ranges keep the merge well-defined (e.g. drift scale stays within the
 * artifact's 0.9–1.1 hint bounds, and the floor is a Wilson value in 0..1).
 */
export const masterPolicySchema = z.strictObject({
  matchToleranceMs: count(0, 86_400_000),
  severityWeights: z.strictObject({ low: weight, medium: weight, high: weight }),
  confidenceFloor: z.number().finite().min(0).max(1),
  coverageNegativeWeight: weight,
  candidatePromotionContributors: count(1, 1000),
  alignmentSearchMs: count(0, 86_400_000),
  alignmentMinAnchors: count(2, 2000),
  alignmentMinDriftSpanMs: count(0, 86_400_000),
  alignmentMaxScaleDeviation: z.number().finite().min(0).max(0.1),
  // Clients reject snapshots with more than 4,000 filters (§8).
  maxServedFilters: count(1, 4000),
  syncRunsPerDay: count(1, 1_000_000),
  syncMaxBatchesPerRun: count(1, 1_000_000),
  syncRunWindowSeconds: count(1, 86_400),
  versionChecksPerHour: count(1, 1_000_000),
  submissionsPerDay: count(1, 1_000_000),
});

/** Validate a parsed `MASTER_POLICY` JSON value; throws a ZodError when invalid. */
export function parseMasterPolicy(value: unknown): MasterPolicy {
  return masterPolicySchema.parse(value);
}
