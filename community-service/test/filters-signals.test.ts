import { describe, expect, it } from 'vitest';
import { DEFAULT_MERGE_POLICY, deriveFilterId, mergeVersion, wilson, type CoverageInput, type MergeResult } from '../src/filters';
import { digestOf, dispute, mergeInput, persist, submission, support, VERSION_ID, vote } from './helpers/filters';

const F = deriveFilterId(VERSION_ID, digestOf('sub_A'), 'e1');
const covered: CoverageInput[] = [{ category: 'violence', intervals: [{ startMs: 0, endMs: 10_000 }] }];
const row = (result: MergeResult, submissionId: string, entryId: string) =>
  result.attestations.find(item => item.submissionId === submissionId && item.entryId === entryId);

describe('signal precedence (§6.8)', () => {
  const result = mergeVersion(mergeInput([
    submission('sub_A', 'c_A', [support('e1', 1000, 2000)]),
    submission('sub_B', 'c_B', [support('e1', 1000, 2000), dispute('e2', 1000, 2000, { proposedFilterId: F })]),
    submission('sub_C', 'c_C', [dispute('e1', 1000, 2000, { proposedFilterId: F }),
      dispute('e2', 1000, 2000, { kind: 'coverage_negative', proposedFilterId: 'f_unknown' })]),
    submission('sub_D', 'c_D', [dispute('e1', 1000, 2000, { kind: 'coverage_negative', proposedFilterId: F })], { coverage: covered }),
    submission('sub_E', 'c_E', [], { coverage: covered }),
  ], { votes: [vote(F, 'c_A', 'up'), vote(F, 'c_E', 'up'), vote(F, 'c_C', 'down'), vote(F, 'c_G', 'up')] }));
  const [filter] = result.filters;

  it('keeps one signal per contributor: support > dispute > explicit > implicit > vote', () => {
    expect(filter.filterId).toBe(F);
    expect(filter.support).toEqual({ attestations: 2, upvotes: 1 });
    expect(filter.negative).toEqual({ downvotes: 0, disputes: 1, coverageNegatives: 2 });
    expect(row(result, 'sub_B', 'e2')).toMatchObject({ filterId: F, counted: false });
    expect(row(result, 'sub_C', 'e1')).toMatchObject({ kind: 'dispute', counted: true });
    expect(row(result, 'sub_C', 'e2')).toMatchObject({ filterId: F, counted: false });
    expect(row(result, 'sub_D', 'e1')).toMatchObject({ kind: 'coverage_negative', implicit: false, counted: true });
    expect(row(result, 'sub_D', `cov:${F}`)).toBeUndefined();
    expect(row(result, 'sub_E', `cov:${F}`)).toMatchObject({ implicit: true, counted: true });
  });
});

describe('dispute and coverage-negative assignment (§6.8)', () => {
  it('assigns by proposedFilterId of the same category, else leaves the entry unassigned', () => {
    const result = mergeVersion(mergeInput([
      submission('sub_A', 'c_A', [support('e1', 1000, 2000)]),
      submission('sub_B', 'c_B', [dispute('e1', 50_000, 51_000, { proposedFilterId: F }),
        dispute('e2', 1000, 2000, { category: 'language', proposedFilterId: 'f_other' })]),
    ]));
    expect(row(result, 'sub_B', 'e1')).toMatchObject({ filterId: F, counted: true });
    expect(row(result, 'sub_B', 'e2')).toMatchObject({ filterId: null, counted: false });
    expect(result.filters[0].negative.disputes).toBe(1);
  });

  it('follows a retired ID into the filter it merged into', () => {
    const input = mergeInput([
      submission('sub_A', 'c_A', [support('e', 600_000, 605_000)]),
      submission('sub_B', 'c_B', [support('e', 600_600, 605_600)]),
    ]);
    const first = mergeVersion(input), [keep, absorbed] = first.filters.map(item => item.filterId), state = persist(input, first);
    const result = mergeVersion({ ...state, submissions: [...state.submissions,
      submission('sub_C', 'c_C', [support('e', 600_300, 605_300)]),
      submission('sub_D', 'c_D', [dispute('e', 10, 20, { proposedFilterId: absorbed })])] });
    expect(row(result, 'sub_D', 'e')).toMatchObject({ filterId: keep, counted: true });
  });

  /** Filters X (1000–2000) and Y (1000–2800) are both exactly 400 ms from 1400–2400. */
  const twoFilters = mergeInput([
    submission('sub_X', 'c_X', [support('e', 1000, 2000)]), submission('sub_Y', 'c_Y', [support('e', 1000, 2800)]),
  ]);

  it('assigns by closest match and breaks ties by the smaller filterId', () => {
    const result = mergeVersion({ ...twoFilters, submissions: [...twoFilters.submissions,
      submission('sub_D', 'c_D', [dispute('e', 1400, 2400, { proposedFilterId: 'f_unknown' })])] });
    const smaller = result.filters.map(item => item.filterId).sort()[0];
    expect(row(result, 'sub_D', 'e')?.filterId).toBe(smaller);
  });

  it('breaks a sweep tie toward the cluster whose prior ID is the proposed ID', () => {
    const first = mergeVersion(twoFilters), state = persist(twoFilters, first);
    const [x, y] = first.filters.map(item => item.filterId);
    const joined = (proposedFilterId: string) => mergeVersion({ ...state, submissions: [...state.submissions,
      submission('sub_C', 'c_C', [support('e', 1400, 2400, { proposedFilterId })])] })
      .filters.map(item => [item.filterId, item.contributorCount]);
    expect(Object.fromEntries(joined(y))).toEqual({ [x]: 1, [y]: 2 });
    expect(Object.fromEntries(joined(x))).toEqual({ [x]: 2, [y]: 1 });
  });

  it('keeps quarantined attestations from becoming signals or implicit coverage negatives', () => {
    const input = mergeInput([
      submission('sub_A', 'c_A', [support('e1', 1000, 2000)]),
      submission('sub_B', 'c_B', [support('e1', 1000, 2000)], { coverage: covered }),
    ]);
    const result = mergeVersion({ ...input, moderation: { ...input.moderation,
      quarantinedAttestations: [{ submissionId: 'sub_B', entryId: 'e1' }] } });
    expect(result.filters[0]).toMatchObject({ contributorCount: 1, negative: { downvotes: 0, disputes: 0, coverageNegatives: 0 } });
    expect(result.attestations.filter(item => item.submissionId === 'sub_B')).toEqual([]);
  });
});

describe('serving cap, runtime and trust', () => {
  it('serves at most maxServedFilters, highest confidence first', () => {
    const input = mergeInput([
      submission('sub_A', 'c_A', [support('e1', 1000, 2000), support('e2', 9000, 9500)]),
      submission('sub_B', 'c_B', [support('e1', 9000, 9500)]),
    ]);
    expect(mergeVersion(input).servedFilters).toHaveLength(2);
    const capped = mergeVersion(input, { ...DEFAULT_MERGE_POLICY, maxServedFilters: 1 });
    expect(capped.servedFilters.map(item => [item.startMs, item.contributorCount])).toEqual([[9000, 2]]);
    expect(capped.filters.find(item => item.startMs === 1000)?.served).toBe(false);
  });

  it('computes runtimeMs as the integer median of counted durations and trust from contributors', () => {
    const submissions = [
      submission('sub_A', 'c_A', [support('e1', 1000, 2000)], { durationMs: 100 }),
      submission('sub_B', 'c_B', [], { durationMs: 301 }),
      submission('sub_C_old', 'c_C', [], { durationMs: 9_999, acceptedSeq: 1 }),
      submission('sub_C', 'c_C', [], { durationMs: 200, acceptedSeq: 2 }),
    ];
    const votes = [vote(F, 'c_U', 'up'), vote(F, 'c_V', 'down'), vote(F, 'c_A', 'down'), vote(F, 'c_W', 'down'),
      vote(F, 'c_X', 'down', true)];
    const input = mergeInput(submissions, { votes, moderation: { ...mergeInput([]).moderation, quarantinedContributors: ['c_W'] } });
    const result = mergeVersion(input);
    expect(result.runtimeMs).toBe(200);
    expect(result.trust).toBe(wilson(4, 1));
    const even = mergeVersion({ ...input, submissions: [...submissions, submission('sub_D', 'c_D', [], { durationMs: 400 })] });
    expect(even.runtimeMs).toBe(250);
    expect(mergeVersion(mergeInput([])).runtimeMs).toBeNull();
  });
});
