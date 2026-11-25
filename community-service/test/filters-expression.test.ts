import { describe, expect, it } from 'vitest';
import { deriveFilterId, mergeVersion, SNAPSHOT_MAX_BYTES, snapshotBytes, snapshotFilter } from '../src/filters';
import { digestOf, mergeInput, persist, shuffleInput, submission, support, VERSION_ID, vote } from './helpers/filters';

const word = (entryId: string, expression: string | null) => support(entryId, 1000, 2000,
  { category: 'language', subcategory: 'swearing', action: 'mute', expression });
const contributors = (expressions: (string | null)[]) => expressions.map((expression, index) =>
  submission(`sub_${index}`, `c_${index}`, [word('e1', expression)]));
const merged = (expressions: (string | null)[]) => mergeVersion(mergeInput(contributors(expressions))).filters[0];

describe('expression vote (§6.7)', () => {
  it('takes the plurality case-insensitive group and its most common spelling', () => {
    expect(merged(['Damn', 'damn', 'DAMN', 'damn', 'Hell', 'Hell'])).toMatchObject({ expression: 'damn', contributorCount: 6 });
    expect(merged(['STRASSE', 'straße', 'Hell'])).toMatchObject({ expression: 'STRASSE' });
  });

  it('ignores members without an expression and is null when none carries one', () => {
    expect(merged([null, null, 'Hell'])).toMatchObject({ expression: 'Hell', contributorCount: 3 });
    expect(merged([null, null]).expression).toBeNull();
  });

  it('breaks ties by the incumbent, then by the smallest value', () => {
    expect(merged(['Hell', 'Damn']).expression).toBe('Damn');
    expect(merged(['damn', 'Damn']).expression).toBe('Damn');
    const first = mergeInput(contributors(['Hell']));
    const incumbent = persist(first, mergeVersion(first));
    const tied = { ...incumbent, submissions: [...incumbent.submissions, ...contributors([null, 'Damn']).slice(1)] };
    expect(mergeVersion(tied).filters[0].expression).toBe('Hell');
    const lower = mergeInput(contributors(['damn']));
    const spelled = persist(lower, mergeVersion(lower));
    const spelling = { ...spelled, submissions: [...spelled.submissions, ...contributors([null, 'Damn']).slice(1)] };
    expect(mergeVersion(spelling).filters[0].expression).toBe('damn');
  });

  it('records the expression as the incumbent and in the snapshot, independent of input order', () => {
    const input = mergeInput(contributors(['Damn', 'damn', 'Hell', 'damn']));
    const result = mergeVersion(input);
    expect(result.registry[0].incumbentExpression).toBe('damn');
    expect(snapshotFilter(result.filters[0]).expression).toBe('damn');
    for (const seed of [3, 17, 99]) expect(mergeVersion(shuffleInput(input, seed)).servedContent).toBe(result.servedContent);
  });

  it('advances the revision when only the expression changes', () => {
    const input = mergeInput(contributors(['Damn']));
    const state = persist(input, mergeVersion(input));
    expect(mergeVersion(state).revision).toEqual({ revision: 1, changed: false });
    const changed = { ...state, submissions: contributors(['Hell']) };
    expect(mergeVersion(changed).revision).toEqual({ revision: 2, changed: true });
  });
});

describe('snapshot size cap (§7.3)', () => {
  it('stops serving the lowest-priority filters until the worst-case snapshot fits', () => {
    const longest = '😀'.repeat(64);
    const entries = Array.from({ length: 4000 }, (_, index) => support(`e${index}`, index * 2000, index * 2000 + 1000,
      { category: 'language', subcategory: 'swearing', action: 'mute', expression: longest }));
    const votes = [vote(deriveFilterId(VERSION_ID, digestOf('sub_0'), 'e3999'), 'c_fan', 'up')];
    const result = mergeVersion(mergeInput([submission('sub_0', 'c_0', entries)], { votes }));
    expect(result.filters).toHaveLength(4000);
    const served = result.servedFilters;
    expect(served.length).toBeGreaterThan(2000);
    expect(served.length).toBeLessThan(4000);
    const bytes = snapshotBytes(result.servedContent, Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER);
    expect(bytes.byteLength).toBeLessThanOrEqual(SNAPSHOT_MAX_BYTES);
    // The upvoted filter has the highest confidence and stays; ties keep the earliest filters.
    expect(served.at(-1)!.startMs).toBe(3999 * 2000);
    expect(served.slice(0, -1).map(filter => filter.startMs)).toEqual(served.slice(0, -1).map((_, index) => index * 2000));
    // Adding back the next filter in serving order would exceed the limit.
    const next = result.filters.find(filter => !filter.served)!;
    const grown = JSON.parse(result.servedContent) as { filters: unknown[] };
    grown.filters.push(snapshotFilter(next));
    expect(() => snapshotBytes(JSON.stringify(grown), Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER)).toThrow('snapshot_too_large');
  });
});
