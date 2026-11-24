import { compareAscii } from './stats';
import type { EntryInput, Fit, IgnoredReason, MatchKey, ModerationInput, SubmissionInput, SubmissionResult } from './types';

/** A non-quarantined entry of a counted submission, aligned once its fit is known. */
export interface PreparedEntry {
  submission: PreparedSubmission;
  entry: EntryInput;
  alignedStartMs: number;
  alignedEndMs: number;
  /** 1 only for support entries that may create or move a filter. */
  weight: 0 | 1;
  ignoredReason: IgnoredReason | null;
}

export interface PreparedSubmission {
  input: SubmissionInput;
  /** Shares a reference key with the version (§6.2). */
  identity: boolean;
  fit: Fit;
  entries: PreparedEntry[];
  /** Quarantined entries: never a signal, but they still block implicit coverage negatives. */
  blockers: PreparedEntry[];
}

/** Equality key for a tier 1–3 match key (§3.2). */
export function matchKeyIdentity(key: MatchKey) {
  return JSON.stringify([key.type, key.algorithm ?? null, key.value, key.sizeBytes ?? null]);
}

/**
 * Select the counted submission per contributor (§6.1): the highest `acceptedSeq`
 * among submissions not excluded by moderation. Quarantined entries are dropped and
 * unverified re-shared support is marked weight 0.
 */
export function selectCounted(submissions: readonly SubmissionInput[], referenceKeys: readonly MatchKey[],
  moderation: ModerationInput) {
  const quarantinedContributors = new Set(moderation.quarantinedContributors);
  const quarantinedSubmissions = new Set(moderation.quarantinedSubmissions);
  const quarantinedEntries = new Map<string, Set<string>>();
  for (const { submissionId, entryId } of moderation.quarantinedAttestations) {
    quarantinedEntries.set(submissionId, (quarantinedEntries.get(submissionId) ?? new Set()).add(entryId));
  }
  const references = new Set(referenceKeys.map(matchKeyIdentity));
  const latest = new Map<string, SubmissionInput>();
  const excluded = (submission: SubmissionInput) =>
    quarantinedContributors.has(submission.contributorId) || quarantinedSubmissions.has(submission.submissionId);
  for (const submission of submissions) {
    if (excluded(submission)) continue;
    const current = latest.get(submission.contributorId);
    if (!current || submission.acceptedSeq > current.acceptedSeq ||
        (submission.acceptedSeq === current.acceptedSeq && submission.submissionId > current.submissionId)) {
      latest.set(submission.contributorId, submission);
    }
  }
  const counted: PreparedSubmission[] = [];
  for (const input of [...latest.values()].sort((a, b) => compareAscii(a.submissionId, b.submissionId))) {
    const submission: PreparedSubmission = {
      input, identity: input.matchKeys.some(key => references.has(matchKeyIdentity(key))),
      fit: { mode: 'none', offsetMs: 0, scale: 1, anchors: 0 }, entries: [], blockers: [],
    };
    const quarantined = quarantinedEntries.get(input.submissionId);
    for (const entry of input.entries) {
      if (quarantined?.has(entry.entryId)) {
        submission.blockers.push({ submission, entry, alignedStartMs: entry.startMs, alignedEndMs: entry.endMs,
          weight: 0, ignoredReason: null });
        continue;
      }
      const unverified = entry.kind === 'support' && entry.origin === 'downloaded' && !entry.locallyVerified;
      submission.entries.push({ submission, entry, alignedStartMs: entry.startMs, alignedEndMs: entry.endMs,
        weight: entry.kind === 'support' && !unverified ? 1 : 0,
        ignoredReason: unverified ? 'unverified_reshare' : null });
    }
    counted.push(submission);
  }
  const countedIds = new Set(counted.map(submission => submission.input.submissionId));
  const states: SubmissionResult[] = submissions.map(submission => ({
    submissionId: submission.submissionId,
    state: excluded(submission) ? 'quarantined' as const : countedIds.has(submission.submissionId) ? 'counted' as const : 'superseded' as const,
    fit: null,
  })).sort((a, b) => compareAscii(a.submissionId, b.submissionId));
  return { counted, states };
}

/** Canonical attestation order (§6.1): aligned start, aligned end, category, digest, entryId. */
export function compareCanonical(a: PreparedEntry, b: PreparedEntry) {
  return a.alignedStartMs - b.alignedStartMs || a.alignedEndMs - b.alignedEndMs ||
    compareAscii(a.entry.category, b.entry.category) ||
    compareAscii(a.submission.input.digest, b.submission.input.digest) ||
    compareAscii(a.entry.entryId, b.entry.entryId);
}
