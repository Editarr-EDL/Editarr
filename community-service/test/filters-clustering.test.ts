import { describe, expect, it } from 'vitest';
import { mergeVersion, type EntryInput, type Severity } from '../src/filters';
import { mergeInput, persist, submission, support } from './helpers/filters';

const merge = (...entries: EntryInput[][]) =>
  mergeVersion(mergeInput(entries.map((items, index) => submission(`sub_${index}`, `c_${index}`, items))));

describe('same-filter rule (§6.5)', () => {
  it('joins at exactly ±N and splits just beyond it', () => {
    expect(merge([support('e', 600_000, 605_000)], [support('e', 600_500, 605_500)]).filters).toHaveLength(1);
    expect(merge([support('e', 600_000, 605_000)], [support('e', 600_501, 605_000)]).filters).toHaveLength(2);
    expect(merge([support('e', 600_000, 605_000)], [support('e', 600_000, 605_501)]).filters).toHaveLength(2);
    expect(merge([support('e', 600_000, 605_000)], [support('e', 600_000, 604_499)]).filters).toHaveLength(2);
  });

  it('never merges different top-level categories, even at identical times', () => {
    const result = merge([support('e', 1000, 2000)], [support('e', 1000, 2000, { category: 'language', subcategory: 'swearing' })]);
    expect(result.filters.map(filter => [filter.category, filter.contributorCount])).toEqual([['language', 1], ['violence', 1]]);
  });

  it('chooses the closest candidate cluster', () => {
    // Both clusters are candidates (max Δ 500 and 400); the entry joins the closer second one.
    const result = merge([support('e', 10_000, 20_000)], [support('e', 10_300, 20_900)], [support('e', 10_400, 20_500)]);
    expect(result.filters.map(filter => [filter.startMs, filter.endMs, filter.contributorCount]))
      .toEqual([[10_000, 20_000, 1], [10_350, 20_700, 2]]);
  });

  it('counts one contributor once per filter and stores repeats as duplicates', () => {
    const result = merge([support('e1', 1000, 2000), support('e2', 1100, 2100)], [support('e', 1050, 2050)]);
    expect(result.filters).toHaveLength(1);
    expect(result.filters[0].contributorCount).toBe(2);
    expect(result.attestations.find(row => row.entryId === 'e2')).toMatchObject(
      { weight: 0, ignoredReason: 'duplicate', filterId: result.filters[0].filterId, counted: false });
  });

  it('ignores re-shared downloads that were not locally verified', () => {
    const reshare = { origin: 'downloaded' as const, locallyVerified: false };
    const result = merge([support('e', 1000, 2000)], [support('e', 1000, 2000, reshare)], [support('e', 9000, 9500, reshare)]);
    expect(result.filters).toHaveLength(1);
    expect(result.filters[0].contributorCount).toBe(1);
    expect(result.attestations.filter(row => row.ignoredReason === 'unverified_reshare')).toHaveLength(2);
    const verified = merge([support('e', 1000, 2000)], [support('e', 1000, 2000, { ...reshare, locallyVerified: true })]);
    expect(verified.filters[0].contributorCount).toBe(2);
  });

  it('does not count an unverified re-share as an implicit coverage negative', () => {
    const coverage = [{ category: 'violence', intervals: [{ startMs: 0, endMs: 10_000 }] }];
    const result = mergeVersion(mergeInput([
      submission('sub_A', 'c_A', [support('e', 1000, 2000)]),
      submission('sub_B', 'c_B', [support('e', 1000, 2000, { origin: 'downloaded', locallyVerified: false })], { coverage }),
      submission('sub_C', 'c_C', [], { coverage }),
    ]));
    expect(result.filters[0].negative.coverageNegatives).toBe(1);
  });

  it('counts only the latest accepted submission per contributor', () => {
    const input = mergeInput([
      submission('sub_old', 'c_A', [support('e', 1000, 2000)], { acceptedSeq: 1 }),
      submission('sub_new', 'c_A', [support('e', 50_000, 51_000)], { acceptedSeq: 7 }),
      submission('sub_other', 'c_B', [support('e', 1000, 2000)], { acceptedSeq: 3 }),
    ]);
    const result = mergeVersion(input);
    expect(result.filters.map(filter => [filter.startMs, filter.contributorCount])).toEqual([[1000, 1], [50_000, 1]]);
    expect(result.submissions.map(item => [item.submissionId, item.state])).toEqual(
      [['sub_new', 'counted'], ['sub_old', 'superseded'], ['sub_other', 'counted']]);
    expect(result.submissions.find(item => item.submissionId === 'sub_old')?.fit).toBeNull();
  });
});

describe('classification votes (§6.7)', () => {
  const classify = (values: { subcategory?: string; severity?: Severity; action?: 'skip' | 'mute' }[]) =>
    merge(...values.map((value, index) => [support('e', 1000 + index, 2000 + index, value)]));

  it('uses the plurality subcategory, then the first canonical member, then ASCII order', () => {
    expect(classify([{ subcategory: 'kicking' }, { subcategory: 'punching' }, { subcategory: 'punching' }]).filters[0].subcategory).toBe('punching');
    expect(classify([{ subcategory: 'kicking' }, { subcategory: 'punching' }]).filters[0].subcategory).toBe('kicking');
    expect(classify([{ subcategory: 'stabbing' }, { subcategory: 'punching' }]).filters[0].subcategory).toBe('stabbing');
    expect(classify([{ subcategory: 'stabbing' }, { subcategory: 'punching' }, { subcategory: 'punching' },
      { subcategory: 'kicking' }, { subcategory: 'kicking' }]).filters[0].subcategory).toBe('kicking');
  });

  it('keeps the incumbent subcategory and action on a tie', () => {
    const input = mergeInput([submission('sub_0', 'c_0', [support('e', 1000, 2000, { subcategory: 'kicking', action: 'mute' })])]);
    const state = persist(input, mergeVersion(input));
    const tied = { ...state, submissions: [...state.submissions,
      submission('sub_1', 'c_1', [support('e', 900, 1900, { subcategory: 'punching', action: 'skip' })])] };
    expect(mergeVersion(tied).filters[0]).toMatchObject({ subcategory: 'kicking', action: 'mute' });
    const fresh = { ...tied, registry: [], submissions: tied.submissions.map(item =>
      ({ ...item, entries: item.entries.map(entry => ({ ...entry, priorFilterId: null })) })) };
    expect(mergeVersion(fresh).filters[0]).toMatchObject({ subcategory: 'punching', action: 'skip' });
  });

  it('weights severity low=1, medium=1, high=3 and breaks ties toward the more severe value', () => {
    expect(classify([{ severity: 'low' }, { severity: 'low' }, { severity: 'low' }, { severity: 'high' }]).filters[0].severity).toBe('high');
    expect(classify([{ severity: 'low' }, { severity: 'low' }, { severity: 'low' }, { severity: 'low' }, { severity: 'high' }])
      .filters[0].severity).toBe('low');
    expect(classify([{ severity: 'low' }, { severity: 'medium' }]).filters[0].severity).toBe('medium');
    expect(classify([{ severity: 'medium' }, { severity: 'medium' }, { severity: 'high' }]).filters[0].severity).toBe('high');
  });
});
