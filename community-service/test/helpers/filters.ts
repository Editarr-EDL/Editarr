import { createHash } from 'node:crypto';
import { deriveFilterId, type EntryInput, type MatchKey, type MergeInput, type MergeResult, type SnapshotHeader, type SubmissionInput, type VoteInput } from '../../src/filters';

export const VERSION_ID = 'v_12ab';
export const REFERENCE_KEY: MatchKey = { type: 'sha256_sampled', value: 'a'.repeat(64), sizeBytes: 4_823_749_120 };
export const OTHER_KEY: MatchKey = { type: 'md5_quick', value: 'b'.repeat(32), sizeBytes: 4_000_000_000 };

type EntryOptions = Partial<Omit<EntryInput, 'entryId' | 'startMs' | 'endMs'>>;

export function support(entryId: string, startMs: number, endMs: number, options: EntryOptions = {}): EntryInput {
  return { entryId, kind: 'support', proposedFilterId: null, startMs, endMs, category: 'violence',
    subcategory: 'punching', severity: 'medium', action: 'skip', origin: 'manual', locallyVerified: true,
    priorFilterId: null, ...options };
}

export function dispute(entryId: string, startMs: number, endMs: number, options: EntryOptions = {}): EntryInput {
  return support(entryId, startMs, endMs, { kind: 'dispute', subcategory: null, severity: null, action: null,
    origin: 'downloaded', locallyVerified: false, ...options });
}

export function digestOf(submissionId: string) {
  return createHash('sha256').update(submissionId).digest('hex');
}

export function submission(submissionId: string, contributorId: string, entries: EntryInput[],
  options: Partial<SubmissionInput> = {}): SubmissionInput {
  return { submissionId, digest: digestOf(submissionId), contributorId, acceptedSeq: 1, durationMs: 8_160_512,
    matchKeys: [REFERENCE_KEY], coverage: [], entries, ...options };
}

export function vote(filterId: string, contributorId: string, value: 'up' | 'down', excluded = false): VoteInput {
  return { filterId, contributorId, vote: value, excluded };
}

export const HEADER: SnapshotHeader = {
  service: 'https://community.example',
  work: { workId: 'w_77', kind: 'movie', tmdb: '603', tvdb: null, season: null, episode: null, lastEpisode: null,
    title: 'The Matrix', year: 1999 },
  versionInfo: { status: 'confirmed', aliasId: 'theatrical', label: 'Theatrical', labelText: null },
};

export function mergeInput(submissions: SubmissionInput[], options: Partial<MergeInput> = {}): MergeInput {
  return { versionId: VERSION_ID, header: HEADER, referenceKeys: [REFERENCE_KEY], submissions, votes: [],
    moderation: { versionQuarantined: false, quarantinedContributors: [], quarantinedSubmissions: [], quarantinedAttestations: [] },
    registry: [], previous: null, ...options };
}

/** Write a result back as the next recompute's persisted state, as the service would. */
export function persist(input: MergeInput, result: MergeResult): MergeInput {
  const assigned = new Map(result.attestations.filter(row => !row.implicit)
    .map(row => [`${row.submissionId}:${row.entryId}`, row.filterId]));
  return { ...input, registry: result.registry,
    previous: { revision: result.revision.revision, servedContent: result.servedContent },
    submissions: input.submissions.map(item => ({ ...item, entries: item.entries.map(entry => {
      const key = `${item.submissionId}:${entry.entryId}`;
      return assigned.has(key) ? { ...entry, priorFilterId: assigned.get(key)! } : entry;
    }) })) };
}

/** Deterministic Fisher–Yates shuffle. */
export function shuffle<T>(values: readonly T[], seed: number): T[] {
  const result = [...values];
  let state = seed >>> 0 || 1;
  for (let i = result.length - 1; i > 0; i--) {
    state ^= state << 13; state ^= state >>> 17; state ^= state << 5; state >>>= 0;
    const j = state % (i + 1);
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}

/** Shuffle every order-carrying list in a merge input. */
export function shuffleInput(input: MergeInput, seed: number): MergeInput {
  return { ...input,
    submissions: shuffle(input.submissions, seed).map((item, index) => ({ ...item,
      entries: shuffle(item.entries, seed + index + 1), coverage: shuffle(item.coverage, seed + index + 7),
      matchKeys: shuffle(item.matchKeys, seed + index + 3) })),
    votes: shuffle(input.votes, seed + 11), registry: shuffle(input.registry, seed + 13),
    referenceKeys: shuffle(input.referenceKeys, seed + 17),
    moderation: { ...input.moderation,
      quarantinedContributors: shuffle(input.moderation.quarantinedContributors, seed + 19),
      quarantinedSubmissions: shuffle(input.moderation.quarantinedSubmissions, seed + 23),
      quarantinedAttestations: shuffle(input.moderation.quarantinedAttestations, seed + 29) } };
}

export const workedExampleViolenceId = deriveFilterId(VERSION_ID, digestOf('sub_C'), 'e1');

/** The §6.10 worked example: A, B, C identity-aligned, D fitted, E/F voters, G covering violence. */
export function workedExample() {
  return mergeInput([
    submission('sub_A', 'c_A', [
      support('e1', 600_000, 605_000),
      support('e2', 1_200_000, 1_203_000, { category: 'language', subcategory: 'swearing', severity: 'low', action: 'mute',
        expression: 'Damn' }),
    ]),
    submission('sub_B', 'c_B', [
      support('e1', 600_300, 605_400, { severity: 'high' }),
      dispute('e2', 1_200_000, 1_203_000, { category: 'language' }),
    ]),
    submission('sub_C', 'c_C', [
      support('e1', 599_800, 604_800, { subcategory: 'kicking', severity: 'low' }),
      dispute('e2', 1_200_100, 1_203_100, { category: 'language' }),
    ]),
    submission('sub_D', 'c_D', [support('e1', 598_800, 603_900, { action: 'mute' })], { matchKeys: [OTHER_KEY] }),
    submission('sub_G', 'c_G', [], { coverage: [{ category: 'violence', intervals: [{ startMs: 0, endMs: 3_600_000 }] }] }),
  ], { votes: [vote(workedExampleViolenceId, 'c_E', 'up'), vote(workedExampleViolenceId, 'c_F', 'down')] });
}
