/** Input and output shapes of the master filter merge engine (contract §6). */

export type Severity = 'low' | 'medium' | 'high';
export type FilterAction = 'skip' | 'mute';
export type EntryKind = 'support' | 'dispute' | 'coverage_negative';
export type EvidenceOrigin = 'model' | 'manual' | 'downloaded';
export type FitMode = 'identity' | 'offset' | 'drift' | 'none';
export type IgnoredReason = 'unverified_reshare' | 'duplicate';
export type SubmissionState = 'counted' | 'superseded' | 'quarantined';
export type RegistryState = 'active' | 'retired' | 'quarantined';

/** A tier 1–3 match key (§3.2). Hash keys carry `sizeBytes`; stream keys carry `algorithm`. */
export interface MatchKey {
  type: string;
  value: string;
  algorithm?: string | null;
  sizeBytes?: number | null;
}

export interface EntryInput {
  entryId: string;
  kind: EntryKind;
  proposedFilterId: string | null;
  startMs: number;
  endMs: number;
  category: string;
  subcategory: string | null;
  severity: Severity | null;
  action: FilterAction | null;
  origin: EvidenceOrigin;
  locallyVerified: boolean;
  /** The filter ID this attestation was last assigned to (§6.6), or null. */
  priorFilterId: string | null;
}

export interface CoverageInput {
  category: string;
  intervals: readonly { startMs: number; endMs: number }[];
}

/** One accepted submission stored for the version, counted or not. */
export interface SubmissionInput {
  submissionId: string;
  /** Submission digest: `artifact_digest`, 64 lowercase hex. */
  digest: string;
  contributorId: string;
  acceptedSeq: number;
  durationMs: number;
  matchKeys: readonly MatchKey[];
  coverage: readonly CoverageInput[];
  entries: readonly EntryInput[];
}

export interface VoteInput {
  filterId: string;
  contributorId: string;
  vote: 'up' | 'down';
  /** Moderator `exclude_vote`. */
  excluded: boolean;
}

/** Moderation exclusions (§7.8). Attestation targets are `submissionId:entryId`. */
export interface ModerationInput {
  versionQuarantined: boolean;
  quarantinedContributors: readonly string[];
  quarantinedSubmissions: readonly string[];
  quarantinedAttestations: readonly string[];
}

/** One `filter_registry` row (§6.6). */
export interface RegistryEntry {
  filterId: string;
  createdSeq: number;
  incumbentSubcategory: string | null;
  incumbentAction: FilterAction | null;
  state: RegistryState;
  mergedInto: string | null;
  quarantined: boolean;
}

export interface MergeInput {
  versionId: string;
  /** Tier 1–3 keys of the founding submission (§6.2). */
  referenceKeys: readonly MatchKey[];
  submissions: readonly SubmissionInput[];
  votes: readonly VoteInput[];
  moderation: ModerationInput;
  registry: readonly RegistryEntry[];
  /** `createdSeq` for the first new registry ID. Defaults to the largest registry `createdSeq` + 1. */
  nextCreatedSeq?: number;
  /** The current revision and its merge-derived served content (see `servedContent`). */
  previous?: { revision: number; servedContent: string | null } | null;
}

export interface Fit {
  mode: FitMode;
  offsetMs: number;
  scale: number;
  anchors: number;
}

export interface SubmissionResult {
  submissionId: string;
  state: SubmissionState;
  /** The stored fit for counted submissions, otherwise null. */
  fit: Fit | null;
}

/** One `filter_attestations` row plus derived `submission_entries` columns. */
export interface AttestationResult {
  submissionId: string;
  /** For implicit coverage negatives this is `cov:` + filterId. */
  entryId: string;
  contributorId: string;
  kind: EntryKind;
  implicit: boolean;
  /** Assigned filter, written back as the next prior ID; null when unassigned. */
  filterId: string | null;
  alignedStartMs: number | null;
  alignedEndMs: number | null;
  weight: 0 | 1;
  ignoredReason: IgnoredReason | null;
  /** True when this row is the contributor's one counted signal on `filterId`. */
  counted: boolean;
}

export interface MasterFilter {
  filterId: string;
  mergedIds: string[];
  category: string;
  subcategory: string;
  severity: Severity;
  action: FilterAction;
  startMs: number;
  endMs: number;
  spread: { startMs: number; endMs: number };
  confidence: number;
  contributorCount: number;
  support: { attestations: number; upvotes: number };
  negative: { downvotes: number; disputes: number; coverageNegatives: number };
  lowConfidence: boolean;
  served: boolean;
  quarantined: boolean;
  /** Subcategory and action member counts and severity weighted totals, keys ascending. */
  tallies: {
    subcategory: Record<string, number>;
    severity: Record<string, number>;
    action: Record<string, number>;
  };
}

export interface MergeResult {
  versionId: string;
  /** Every current filter, served or not, in snapshot order. */
  filters: MasterFilter[];
  /** Served filters in snapshot order. */
  servedFilters: MasterFilter[];
  /** Updated registry rows, sorted by filterId. */
  registry: RegistryEntry[];
  submissions: SubmissionResult[];
  attestations: AttestationResult[];
  /** Integer median of counted `durationMs` (§3.5), or null without counted submissions. */
  runtimeMs: number | null;
  /** Version trust (§3.5). */
  trust: number;
  /** Canonical merge-derived served content: runtime, trust and served filters. */
  servedContent: string;
  revision: { revision: number; changed: boolean };
}
