import { describe, expect, it } from 'vitest';
import { deriveFilterId, mergeVersion, type MergeInput } from '../src/filters';
import {
  digestOf, dispute, mergeInput, OTHER_KEY, persist, shuffleInput, submission, support, VERSION_ID, vote,
  workedExample, workedExampleViolenceId,
} from './helpers/filters';

/** The worked example plus superseded, duplicate, re-shared, drifted and quarantined inputs. */
function richInput(): MergeInput {
  const base = workedExample();
  return { ...base,
    submissions: [...base.submissions,
      submission('sub_A_old', 'c_A', [support('e1', 10_000, 11_000)], { acceptedSeq: 0 }),
      submission('sub_H', 'c_H', [support('e1', 600_100, 605_000), support('e2', 600_200, 605_100),
        support('e3', 2_000_000, 2_001_000, { origin: 'downloaded', locallyVerified: false }),
        dispute('e4', 1_200_050, 1_203_050, { category: 'language', kind: 'coverage_negative' })],
      { acceptedSeq: 4, coverage: [{ category: 'language', intervals: [{ startMs: 1_000_000, endMs: 1_300_000 }] }] }),
      submission('sub_I', 'c_I', [support('e1', 599_900, 604_900, { severity: 'high' }), support('e2', 3_000_000, 3_004_000)],
        { matchKeys: [OTHER_KEY], acceptedSeq: 5 }),
      submission('sub_Q', 'c_Q', [support('e1', 3_000_000, 3_004_000)], { acceptedSeq: 6 }),
    ],
    votes: [...base.votes, vote(workedExampleViolenceId, 'c_J', 'up', true), vote(workedExampleViolenceId, 'c_A', 'down')],
    moderation: { ...base.moderation, quarantinedContributors: ['c_Q'], quarantinedAttestations: [{ submissionId: 'sub_B', entryId: 'e9' }] },
  };
}

describe('order independence', () => {
  it('returns identical output for every permutation of the inputs, before and after persisted state', () => {
    const input = richInput(), expected = JSON.stringify(mergeVersion(input));
    for (let seed = 1; seed <= 25; seed++) expect(JSON.stringify(mergeVersion(shuffleInput(input, seed)))).toBe(expected);
    const second = persist(input, mergeVersion(input)), expectedSecond = JSON.stringify(mergeVersion(second));
    for (let seed = 1; seed <= 25; seed++) expect(JSON.stringify(mergeVersion(shuffleInput(second, seed * 7)))).toBe(expectedSecond);
  });
});

describe('moderation recompute', () => {
  it('excludes a quarantined contributor and restores the same result after restore', () => {
    const input = persist(workedExample(), mergeVersion(workedExample())), before = mergeVersion(input);
    // Without B the core moves, so D refits at +1,100 ms and the median start becomes 599,900.
    const quarantined = mergeVersion({ ...input, moderation: { ...input.moderation, quarantinedContributors: ['c_B', 'c_E'] } });
    expect(quarantined.filters[0]).toMatchObject({ filterId: workedExampleViolenceId, contributorCount: 3,
      startMs: 599_900, endMs: 605_000, severity: 'medium', support: { attestations: 3, upvotes: 0 } });
    expect(quarantined.filters[1].negative.disputes).toBe(1);
    expect(quarantined.submissions.find(item => item.submissionId === 'sub_B')?.state).toBe('quarantined');
    expect(quarantined.revision).toEqual({ revision: 2, changed: true });
    const restored = mergeVersion(persist(input, quarantined));
    expect(restored.filters).toEqual(before.filters);
  });

  it('drops a quarantined attestation or submission and excludes moderated votes', () => {
    const input = workedExample();
    const attestation = mergeVersion({ ...input, moderation: { ...input.moderation, quarantinedAttestations: [{ submissionId: 'sub_C', entryId: 'e1' }] } });
    expect(attestation.filters[0]).toMatchObject({ contributorCount: 3, subcategory: 'punching' });
    expect(attestation.filters[0].filterId).not.toBe(workedExampleViolenceId);
    const submissionOut = mergeVersion({ ...input, moderation: { ...input.moderation, quarantinedSubmissions: ['sub_G'] } });
    expect(submissionOut.filters[0].negative.coverageNegatives).toBe(0);
    const excluded = mergeVersion({ ...input, votes: input.votes.map(item => ({ ...item, excluded: true })) });
    expect(excluded.filters[0].support.upvotes + excluded.filters[0].negative.downvotes).toBe(0);
  });
});

describe('stable filter IDs (§6.6)', () => {
  it('derives a new ID from the first canonical member and keeps it while medians move', () => {
    const input = mergeInput([submission('sub_A', 'c_A', [support('e1', 1000, 2000)])]);
    const first = mergeVersion(input);
    const id = deriveFilterId(VERSION_ID, digestOf('sub_A'), 'e1');
    expect(first.filters[0].filterId).toBe(id);
    expect(id).toMatch(/^f_[0-9a-f]{32}$/);
    let state = persist(input, first);
    // Later members sort before A canonically, so only prior-ID inheritance keeps the ID.
    for (const [index, start] of [800, 1300, 1100].entries()) {
      state = { ...state, submissions: [...state.submissions, submission(`sub_${index}`, `c_${index}`, [support('e', start, start + 1000)])] };
      const result = mergeVersion(state);
      expect(result.filters.map(filter => filter.filterId)).toEqual([id]);
      state = persist(state, result);
    }
    expect(state.registry).toEqual([expect.objectContaining({ filterId: id, state: 'active', createdSeq: 1 })]);
  });

  it('retires an absorbed ID into the surviving filter and routes its votes there', () => {
    const input = mergeInput([
      submission('sub_A', 'c_A', [support('e', 600_000, 605_000)]),
      submission('sub_B', 'c_B', [support('e', 600_600, 605_600)]),
    ]);
    const first = mergeVersion(input);
    const [keep, absorbed] = first.filters.map(filter => filter.filterId);
    expect(first.filters).toHaveLength(2);
    const state = persist(input, first);
    // C bridges both filters into one cluster; the createdSeq tie keeps the older ID.
    const bridged = { ...state, votes: [vote(absorbed, 'c_V', 'up')],
      submissions: [...state.submissions, submission('sub_C', 'c_C', [support('e', 600_300, 605_300)])] };
    const merged = mergeVersion(bridged);
    expect(merged.filters).toHaveLength(1);
    expect(merged.filters[0]).toMatchObject({ filterId: keep, mergedIds: [absorbed], contributorCount: 3, support: { upvotes: 1 } });
    expect(merged.registry.find(row => row.filterId === absorbed)).toMatchObject({ state: 'retired', mergedInto: keep });
    const again = mergeVersion(persist(bridged, merged));
    expect(again.registry.find(row => row.filterId === absorbed)).toMatchObject({ state: 'retired', mergedInto: keep });
    expect(again.revision).toEqual({ revision: merged.revision.revision, changed: false });
  });

  it('leaves mergedInto null without a strict majority of former members', () => {
    const registryRow = (filterId: string, createdSeq: number) => ({ filterId, createdSeq, incumbentSubcategory: null,
      incumbentAction: null, state: 'active' as const, mergedInto: null, quarantined: false });
    const member = (id: string, start: number, priorFilterId: string) =>
      submission(`sub_${id}`, `c_${id}`, [support('e', start, start + 1000, { priorFilterId })]);
    const input = mergeInput([member('1', 1000, 'f_x'), member('2', 1000, 'f_x'), member('3', 1000, 'f_z'),
      member('4', 50_000, 'f_y'), member('5', 50_000, 'f_y'), member('6', 50_000, 'f_z')],
    { registry: [registryRow('f_x', 1), registryRow('f_y', 2), registryRow('f_z', 3)] });
    const split = mergeVersion(input);
    expect(split.filters.map(filter => filter.filterId)).toEqual(['f_x', 'f_y']);
    expect(split.registry.find(row => row.filterId === 'f_z')).toMatchObject({ state: 'retired', mergedInto: null });
    const majority = mergeVersion({ ...input, submissions: [...input.submissions, member('7', 1000, 'f_z')] });
    expect(majority.registry.find(row => row.filterId === 'f_z')).toMatchObject({ state: 'retired', mergedInto: 'f_x' });
    expect(majority.filters[0].mergedIds).toEqual(['f_z']);
  });

  it('derives a different ID when the first member\'s derived ID is already claimed', () => {
    const claimed = deriveFilterId(VERSION_ID, digestOf('sub_A'), 'e1');
    const input = mergeInput([
      submission('sub_B', 'c_B', [support('e', 1000, 2000, { priorFilterId: claimed })]),
      submission('sub_A', 'c_A', [support('e1', 5000, 6000)]),
    ], { registry: [{ filterId: claimed, createdSeq: 1, incumbentSubcategory: null, incumbentAction: null,
      state: 'active', mergedInto: null, quarantined: false }] });
    const counter = deriveFilterId(VERSION_ID, digestOf('sub_A'), 'e1', ['1']);
    expect(mergeVersion(input).filters.map(filter => filter.filterId)).toEqual([claimed, counter]);
    const withSecond = { ...input, submissions: [...input.submissions, submission('sub_C', 'c_C', [support('e', 5100, 6100)])] };
    expect(mergeVersion(withSecond).filters.map(filter => filter.filterId))
      .toEqual([claimed, deriveFilterId(VERSION_ID, digestOf('sub_C'), 'e')]);
  });

  it('advances the revision only when served content changes', () => {
    const input = workedExample(), first = mergeVersion(input);
    expect(first.revision).toEqual({ revision: 1, changed: true });
    const state = persist(input, first);
    expect(mergeVersion(state).revision).toEqual({ revision: 1, changed: false });
    const voted = mergeVersion({ ...state, votes: [...state.votes, vote(workedExampleViolenceId, 'c_K', 'up')] });
    expect(voted.revision).toEqual({ revision: 2, changed: true });
  });
});
