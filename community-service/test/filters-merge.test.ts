import { describe, expect, it } from 'vitest';
import { deriveFilterId, mergeVersion, snapshotFilter, wilson } from '../src/filters';
import { digestOf, mergeInput, submission, support, VERSION_ID, vote, workedExample, workedExampleViolenceId } from './helpers/filters';

const violenceId = workedExampleViolenceId;

describe('§6.10 worked example', () => {
  const result = mergeVersion(workedExample());
  const [violence, language] = result.filters;

  it('fits D by offset +1,200 ms and keeps A, B, C and G identity-aligned', () => {
    const fits = Object.fromEntries(result.submissions.map(item => [item.submissionId, item.fit]));
    expect(fits.sub_D).toEqual({ mode: 'offset', offsetMs: 1200, scale: 1, anchors: 1 });
    for (const id of ['sub_A', 'sub_B', 'sub_C', 'sub_G']) expect(fits[id]?.mode).toBe('identity');
    const aligned = result.attestations.find(row => row.submissionId === 'sub_D' && row.entryId === 'e1');
    expect([aligned?.alignedStartMs, aligned?.alignedEndMs]).toEqual([600_000, 605_100]);
  });

  it('produces the documented violence filter exactly', () => {
    expect(snapshotFilter(violence)).toEqual({
      filterId: violenceId, mergedIds: [], category: 'violence', subcategory: 'punching', severity: 'high', action: 'skip',
      startMs: 600_000, endMs: 605_050, spread: { startMs: 300, endMs: 350 }, confidence: 0.393857102, contributorCount: 4,
      support: { attestations: 4, upvotes: 1 }, negative: { downvotes: 1, disputes: 0, coverageNegatives: 1 }, lowConfidence: false,
    });
    expect(violence.served).toBe(true);
    expect(violence.tallies).toEqual({ subcategory: { kicking: 1, punching: 3 }, severity: { high: 3, low: 1, medium: 2 },
      action: { mute: 1, skip: 3 } });
    expect(result.attestations).toContainEqual(expect.objectContaining({
      submissionId: 'sub_G', entryId: `cov:${violenceId}`, kind: 'coverage_negative', implicit: true, counted: true }));
  });

  it('serves the disputed language filter flagged Low Confidence', () => {
    expect(language).toMatchObject({ category: 'language', subcategory: 'swearing', contributorCount: 1,
      confidence: 0.117618231, lowConfidence: true, served: true,
      negative: { downvotes: 0, disputes: 2, coverageNegatives: 0 } });
    expect(result.servedFilters.map(filter => filter.filterId)).toEqual([violenceId, language.filterId]);
  });
});

describe('Wilson confidence and serving floor', () => {
  it.each([[1, 0, 0.207654955], [1, 1, 0.150035709], [1, 2, 0.117618231], [1, 8, 0.051366601],
    [1, 9, 0.046964148], [3, 0, 0.375528264], [5, 1.25, 0.393857102]])('wilson(%d, %d) = %d', (s, neg, expected) => {
    expect(wilson(s, neg)).toBe(expected);
  });

  const withDownvotes = (count: number) => {
    const id = deriveFilterId(VERSION_ID, digestOf('sub_A'), 'e1');
    const votes = Array.from({ length: count }, (_, index) => vote(id, `c_voter${index}`, 'down'));
    return mergeVersion(mergeInput([submission('sub_A', 'c_A', [support('e1', 1000, 2000)])], { votes })).filters[0];
  };

  it('keeps a single-support filter at 8 negatives and drops it from serving at 9', () => {
    expect(withDownvotes(8)).toMatchObject({ confidence: 0.051366601, served: true, lowConfidence: true });
    const dropped = withDownvotes(9);
    expect(dropped).toMatchObject({ confidence: 0.046964148, served: false, lowConfidence: true });
  });

  it('flags Low Confidence only when negatives exceed attestations plus upvotes', () => {
    expect(withDownvotes(0).lowConfidence).toBe(false);
    expect(withDownvotes(1)).toMatchObject({ confidence: 0.150035709, lowConfidence: false });
    const covered = mergeVersion(mergeInput([
      submission('sub_A', 'c_A', [support('e1', 1000, 2000)]),
      ...['c_X', 'c_Y', 'c_Z', 'c_W', 'c_V'].map(id => submission(`sub_${id}`, id, [],
        { coverage: [{ category: 'violence', intervals: [{ startMs: 0, endMs: 5000 }] }] })),
    ])).filters[0];
    expect(covered.negative.coverageNegatives).toBe(5);
    expect(covered.lowConfidence).toBe(true);
    expect(covered.confidence).toBe(wilson(1, 1.25));
  });

  it('never serves quarantined filters or filters of a quarantined version', () => {
    const base = mergeInput([submission('sub_A', 'c_A', [support('e1', 1000, 2000)])]);
    const first = mergeVersion(base);
    const registry = first.registry.map(row => ({ ...row, quarantined: true }));
    expect(mergeVersion({ ...base, registry }).filters[0]).toMatchObject({ served: false, quarantined: true });
    expect(mergeVersion({ ...base, registry }).registry[0].state).toBe('quarantined');
    const version = mergeVersion({ ...base, moderation: { ...base.moderation, versionQuarantined: true } });
    expect(version.filters[0].served).toBe(false);
    expect(version.servedFilters).toEqual([]);
  });
});
