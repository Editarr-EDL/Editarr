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
  coverageNegativeWeight: 0.25,
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
