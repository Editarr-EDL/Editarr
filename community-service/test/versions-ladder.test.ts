import { describe, expect, it } from 'vitest';
import { normalize, normalizedTitle } from '../src/versions/normalize';
import { labelsEqual } from '../src/versions/labels';
import { compareMatches, runtimeAgreement, scoreVersion, type RankedMatch, type VersionEvidence } from '../src/matching/ladder';
import type { EvidenceKeyType } from '../src/versions/keys';

describe('normalize (§3.3)', () => {
  it.each([
    ['Director’s Cut', 'directors cut'], ['  EXTENDED   Edition!', 'extended edition'], ['Amélie', 'amelie'],
    ['Spider-Man: No Way Home', 'spider man no way home'], ['Ⅲ', 'iii'], ["Director's Cut", 'directors cut'],
    ['Directorʼs `Cut`', 'directors cut'], ['', ''], ['  !!  ', ''], ['ＡＢＣ　１２３', 'abc 123'],
  ])('normalizes %j to %j', (input, expected) => expect(normalize(input)).toBe(expected));

  it('bounds titles to 1–256 code points', () => {
    expect(normalizedTitle('?!')).toBeNull();
    expect(normalizedTitle('é'.repeat(256))).toBe('e'.repeat(256));
    expect(normalizedTitle('x'.repeat(257))).toBeNull();
  });
});

describe('label equality (§3.5 rule 3)', () => {
  it('treats null and theatrical as the same and compares unaliased text', () => {
    const none = { aliasId: null, labelText: null };
    expect(labelsEqual(none, { aliasId: 'theatrical', labelText: null })).toBe(true);
    expect(labelsEqual({ aliasId: 'directors_cut', labelText: 'directors cut' }, { aliasId: 'directors_cut', labelText: null })).toBe(true);
    expect(labelsEqual({ aliasId: null, labelText: 'imax' }, { aliasId: null, labelText: 'imax' })).toBe(true);
    expect(labelsEqual({ aliasId: null, labelText: 'imax' }, none)).toBe(false);
    expect(labelsEqual({ aliasId: null, labelText: 'imax' }, { aliasId: 'theatrical', labelText: null })).toBe(false);
    expect(labelsEqual({ aliasId: 'extended', labelText: null }, none)).toBe(false);
  });
});

const theatrical = { aliasId: 'theatrical', labelText: null };
const version = { label: theatrical, runtimeMs: 8_000_000 };
const evidence = (types: EvidenceKeyType[] = [], providers: ('tmdb' | 'tvdb')[] = [], titleMatched = false): VersionEvidence =>
  ({ evidenceTypes: new Set(types), providerTypes: new Set(providers), titleMatched });

describe('match ladder (§4)', () => {
  it('scores every tier with its Match %', () => {
    expect(scoreVersion(evidence(['sha256_full'], ['tmdb']), version, theatrical, null))
      .toEqual({ tier: '1', matchPercent: 100, matchedKeys: ['sha256_full'], runtimeAgreement: 'not_applicable' });
    expect(scoreVersion(evidence(['md5_quick']), version, theatrical, null))
      .toEqual({ tier: '2', matchPercent: 80, matchedKeys: ['md5_quick'], runtimeAgreement: 'not_applicable' });
    expect(scoreVersion(evidence(['video_stream']), version, theatrical, 1))
      .toEqual({ tier: '3', matchPercent: 65, matchedKeys: ['video_stream'], runtimeAgreement: 'not_applicable' });
    expect(scoreVersion(evidence([], ['tmdb', 'tvdb'], true), version, theatrical, 8_001_500))
      .toEqual({ tier: '4', matchPercent: 30, matchedKeys: ['tmdb', 'tvdb'], runtimeAgreement: 'same_cut' });
    expect(scoreVersion(evidence([], [], true), version, { aliasId: null, labelText: null }, 8_025_000))
      .toEqual({ tier: '5', matchPercent: 15, matchedKeys: ['title_year'], runtimeAgreement: 'possible_cut' });
  });

  it('drops tier 4–5 identity to 5% when the runtime is outside tolerance or unknown', () => {
    expect(scoreVersion(evidence([], ['tmdb'], true), version, theatrical, 8_030_001))
      .toEqual({ tier: 'runtime_mismatch', matchPercent: 5, matchedKeys: ['tmdb', 'title_year'], runtimeAgreement: 'outside' });
    expect(scoreVersion(evidence([], [], true), version, theatrical, null))
      .toMatchObject({ tier: 'runtime_mismatch', matchPercent: 5, runtimeAgreement: 'unknown' });
  });

  it('reports other versions of a matched work at 0%', () => {
    expect(scoreVersion(evidence([], ['tmdb']), version, { aliasId: 'extended', labelText: null }, 8_000_000))
      .toEqual({ tier: 'other_version', matchPercent: 0, matchedKeys: ['tmdb'], runtimeAgreement: 'same_cut' });
  });

  it('uses only the best tier and lists every key type that satisfied it', () => {
    expect(scoreVersion(evidence(['audio_stream', 'md5_quick', 'sha256_sampled'], ['tmdb']), version,
      { aliasId: 'extended', labelText: null }, 1)).toEqual({ tier: '2', matchPercent: 80,
      matchedKeys: ['sha256_sampled', 'md5_quick'], runtimeAgreement: 'not_applicable' });
    expect(scoreVersion(evidence(), version, theatrical, 1)).toBeNull();
  });

  it('classifies runtime thresholds as protocol constants', () => {
    expect([2000, 2001, 30_000, 30_001].map(delta => runtimeAgreement(1_000_000 + delta, 1_000_000)))
      .toEqual(['same_cut', 'possible_cut', 'possible_cut', 'outside']);
    expect(runtimeAgreement(1, null)).toBe('unknown');
  });

  it('orders results by Match %, status, runtime agreement, trust and version ID', () => {
    const base: Omit<RankedMatch, 'versionId'> = { tier: '4', matchPercent: 30, matchedKeys: ['tmdb'], runtimeAgreement: 'same_cut',
      status: 'confirmed', trust: 0.2 };
    const items: RankedMatch[] = [
      { ...base, versionId: 'v_f', trust: 0.5 }, { ...base, versionId: 'v_e' },
      { ...base, versionId: 'v_d', runtimeAgreement: 'possible_cut', trust: 0.9 },
      { ...base, versionId: 'v_c', status: 'candidate', trust: 0.9 },
      { ...base, versionId: 'v_a', tier: '2', matchPercent: 80, status: 'candidate', runtimeAgreement: 'not_applicable' },
      { ...base, versionId: 'v_b' },
    ];
    expect(items.sort(compareMatches).map(item => item.versionId)).toEqual(['v_a', 'v_f', 'v_b', 'v_e', 'v_d', 'v_c']);
  });
});
