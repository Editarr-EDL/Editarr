import { describe, expect, it } from 'vitest';
import { alignTime, DEFAULT_MERGE_POLICY, mergeVersion, type EntryInput } from '../src/filters';
import { mergeInput, OTHER_KEY, submission, support } from './helpers/filters';

/** Identity-aligned core filters from two contributors, plus one fitted submission. */
function fitted(coreStarts: number[], entries: EntryInput[], coreDurationMs = 5000) {
  const core = (prefix: string) => coreStarts.map((start, index) => support(`${prefix}${index}`, start, start + coreDurationMs));
  const result = mergeVersion(mergeInput([
    submission('sub_A', 'c_A', core('a')), submission('sub_B', 'c_B', core('b')),
    submission('sub_X', 'c_X', entries, { matchKeys: [OTHER_KEY] }),
  ]));
  return { result, fit: result.submissions.find(item => item.submissionId === 'sub_X')!.fit! };
}

const shifted = (starts: number[], shiftMs: number, durationMs = 5000) =>
  starts.map((start, index) => support(`x${index}`, start - shiftMs, start - shiftMs + durationMs));

describe('alignment fit (§6.3)', () => {
  it('rounds half-up and clamps negative aligned times to zero', () => {
    expect(alignTime(1000, { offsetMs: -1500, scale: 1 })).toBe(0);
    expect(alignTime(1000, { offsetMs: 0, scale: 1.0005 })).toBe(1001);
    expect(alignTime(999, { offsetMs: 0, scale: 1.0005 })).toBe(999);
  });

  it('recovers a pure offset and joins every fitted entry to its core filter', () => {
    const starts = [100_000, 500_000, 900_000, 1_300_000];
    const { result, fit } = fitted(starts, shifted(starts, 1200));
    // Four anchors spanning 1.2 M ms qualify for drift, which recovers scale 1 exactly.
    expect(fit).toEqual({ mode: 'drift', offsetMs: 1200, scale: 1, anchors: 4 });
    expect(result.filters.map(filter => filter.contributorCount)).toEqual([3, 3, 3, 3]);
  });

  it('fits offset plus linear drift with Theil–Sen when anchors span enough', () => {
    const raw = [100_000, 400_000, 700_000, 1_000_000, 1_300_000];
    const toCore = (t: number) => Math.floor(1.001 * t + 2000 + 0.5);
    const coreAt = (prefix: string) => raw.map((t, index) => support(`${prefix}${index}`, toCore(t), toCore(t + 5000)));
    const result = mergeVersion(mergeInput([
      submission('sub_A', 'c_A', coreAt('a')), submission('sub_B', 'c_B', coreAt('b')),
      submission('sub_X', 'c_X', raw.map((t, index) => support(`x${index}`, t, t + 5000)), { matchKeys: [OTHER_KEY] }),
    ]));
    const fit = result.submissions.find(item => item.submissionId === 'sub_X')!.fit!;
    expect(fit.mode).toBe('drift');
    expect(fit.offsetMs).toBe(2000);
    expect(fit.scale).toBeCloseTo(1.001, 12);
    expect(fit.anchors).toBe(3);
    expect(result.filters.map(filter => filter.contributorCount)).toEqual([3, 3, 3, 3, 3]);
  });

  it('falls back to offset-only with fewer than three anchors', () => {
    const { fit } = fitted([100_000, 900_000], [support('x0', 98_000, 103_000), support('x1', 897_900, 902_900)]);
    expect(fit).toEqual({ mode: 'offset', offsetMs: 2050, scale: 1, anchors: 2 });
  });

  it('falls back to offset-only when anchors span less than the drift span', () => {
    const starts = [100_000, 200_000, 300_000];
    const { fit } = fitted(starts, shifted(starts, 700));
    expect(fit).toEqual({ mode: 'offset', offsetMs: 700, scale: 1, anchors: 3 });
  });

  it('falls back to offset-only when the drift scale deviates too far', () => {
    const starts = [100_000, 400_000, 700_000];
    // Slopes 1, 1.0005 and 1.001 give a 1.0005 median, beyond a 0.0001 deviation policy.
    const entries = [support('x0', 99_000, 104_000), support('x1', 399_000, 404_000), support('x2', 698_700, 703_700)];
    const input = mergeInput([
      submission('sub_A', 'c_A', starts.map((s, i) => support(`a${i}`, s, s + 5000))),
      submission('sub_X', 'c_X', entries, { matchKeys: [OTHER_KEY] }),
    ]);
    const fitOf = (maxDeviation: number) => mergeVersion(input, { ...DEFAULT_MERGE_POLICY, alignmentMaxScaleDeviation: maxDeviation })
      .submissions.find(item => item.submissionId === 'sub_X')!.fit!;
    expect(fitOf(0.05).mode).toBe('drift');
    expect(fitOf(0.0001)).toEqual({ mode: 'offset', offsetMs: 1000, scale: 1, anchors: 3 });
  });

  it('ignores outlier pairs when estimating the offset', () => {
    const starts = [100_000, 150_000, 200_000, 250_000];
    const entries = [...shifted(starts.slice(0, 3), 1200), support('x9', 240_000, 245_000)];
    const { result, fit } = fitted(starts, entries);
    expect(fit).toEqual({ mode: 'offset', offsetMs: 1200, scale: 1, anchors: 3 });
    const outlier = result.attestations.find(row => row.submissionId === 'sub_X' && row.entryId === 'x9')!;
    expect(outlier.alignedStartMs).toBe(241_200);
    expect(result.filters.find(filter => filter.startMs === 250_000)?.contributorCount).toBe(2);
  });

  it('uses no fit without candidate pairs or without a core set', () => {
    expect(fitted([100_000], [support('x0', 500_000, 505_000)]).fit).toEqual({ mode: 'none', offsetMs: 0, scale: 1, anchors: 0 });
    const result = mergeVersion(mergeInput([submission('sub_X', 'c_X', [support('x0', 1, 2)], { matchKeys: [OTHER_KEY] })]));
    expect(result.submissions[0].fit).toEqual({ mode: 'none', offsetMs: 0, scale: 1, anchors: 0 });
    expect(result.filters).toHaveLength(1);
  });
});
