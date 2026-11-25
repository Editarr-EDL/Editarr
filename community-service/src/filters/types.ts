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
  /** Normalized word or phrase of a `language` support (§5.2); absent or null otherwise. */
  expression?: string | null;
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

/** One attestation, identified by its submission and entry. */
export interface AttestationRef {
  submissionId: string;
  entryId: string;
}

/** Moderation exclusions (§7.8). */
export interface ModerationInput {
  versionQuarantined: boolean;
  quarantinedContributors: readonly string[];
  quarantinedSubmissions: readonly string[];
  quarantinedAttestations: readonly AttestationRef[];
}

/** Snapshot `work` (§7.3). */
export interface SnapshotWork {
  workId: string;
  kind: 'movie' | 'episode';
  tmdb: string | null;
  tvdb: string | null;
  season: number | null;
  episode: number | null;
  lastEpisode: number | null;
  title: string;
  year: number | null;
}

/** Snapshot fields outside the merge (§7.3); `runtimeMs` and `trust` come from the merge. */
export interface SnapshotHeader {
  service: string;
  work: SnapshotWork;
  versionInfo: { status: 'confirmed' | 'candidate'; aliasId: string | null; label: string | null; labelText: string | null };
}

/** One `filter_registry` row (§6.6). */
export interface RegistryEntry {
  filterId: string;
  createdSeq: number;
  incumbentSubcategory: string | null;
  incumbentAction: FilterAction | null;
  /** The filter's last merged expression (§6.7); absent or null when it had none. */
  incumbentExpression?: string | null;
  state: RegistryState;
  mergedInto: string | null;
  quarantined: boolean;
}

export interface MergeInput {
  versionId: string;
  /** Snapshot header; part of the served content compared for revisions. */
  header: SnapshotHeader;
  /** Tier 1–3 keys of the founding submission (§6.2). */
  referenceKeys: readonly MatchKey[];
  submissions: readonly SubmissionInput[];
  votes: readonly VoteInput[];
  moderation: ModerationInput;
  registry: readonly RegistryEntry[];
  /** `createdSeq` for the first new registry ID. Defaults to the largest registry `createdSeq` + 1. */
  nextCreatedSeq?: number;
  /** The current revision and its served content (`MergeResult.servedContent` when it was stored). */
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
  /** The merged word or phrase (§6.7), or null. */
  expression: string | null;
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
  /**
   * Canonical served content of the current revision: every snapshot field except
   * `revision` and `generatedAt`. While the version is quarantined this is the
   * previous content, so the stored revision is left untouched.
   */
  servedContent: string;
  revision: { revision: number; changed: boolean };
}
