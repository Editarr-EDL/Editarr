import { expect, it } from 'vitest';
import fixture from '../../tests/fixtures/community/compatibility-core-v1.json';
import streamFixture from '../../tests/fixtures/community/compatibility-stream-v1.json';
import { POLICY_VERSION, matchEvidenceSchema } from '../src/catalog/compatibility-inputs';
import { classify, classifyCandidates, compatibilityCacheKey } from '../src/catalog/compatibility';
import { PCM_AUDIO_ALGORITHM } from '../src/catalog/stream-compatibility';

function merge(base: Record<string, unknown>, changes: Record<string, unknown>): Record<string, unknown> {
  const result = { ...base };
  for (const [key, value] of Object.entries(changes)) {
    result[key] = value !== null && typeof value === 'object' && !Array.isArray(value) &&
      result[key] !== null && typeof result[key] === 'object' && !Array.isArray(result[key])
      ? merge(result[key] as Record<string, unknown>, value as Record<string, unknown>) : value;
  }
  return result;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([key, item]) => [key, canonical(item)]));
  return value;
}

it.each(fixture.cases)('classifies shared $name evidence', async (testCase) => {
  const target = matchEvidenceSchema.parse(merge(fixture.target, ('target' in testCase ? testCase.target : {}) ?? {}));
  const candidate = matchEvidenceSchema.parse(merge(target, testCase.candidate));
  const result = classify(target, candidate);
  expect(result.policy_version).toBe(POLICY_VERSION);
  expect(result.video.band).toBe(testCase.video);
  expect(result.audio.band).toBe(testCase.audio);
  expect(result.video.conflicts).toEqual(testCase.video_conflicts);
  expect(result.audio.conflicts).toEqual(testCase.audio_conflicts);
  expect(result.audio.missing).toContain('analyzed_audio_stream');
  expect(result.audio.strong_eligible).toBe(false);
  expect(result.selected).toBe(false);
  const bytes = new TextEncoder().encode(JSON.stringify(canonical({ ...result, policy_version: fixture.policy_version })));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    byte => byte.toString(16).padStart(2, '0')).join('');
  expect(digest).toBe(fixture.dto_sha256[testCase.name as keyof typeof fixture.dto_sha256]);
});

it('bounds candidates and keeps stable ASCII tie-breaks independent of input order', () => {
  const target = matchEvidenceSchema.parse(fixture.target);
  expect(classifyCandidates(target, [['z', target], ['a', target]], 2).map(([id]) => id)).toEqual(['a', 'z']);
  expect(() => classifyCandidates(target, [['a', target], ['b', target]], 1)).toThrow();
  expect(() => classifyCandidates(target, [['a', target], ['a', target]], 2)).toThrow();
  expect(() => classifyCandidates(target, [], 0)).toThrow();
});

it('invalidates cache identity for policy/evidence changes but not object key order', () => {
  const target = matchEvidenceSchema.parse(fixture.target);
  const baseline = compatibilityCacheKey(target, target);
  expect(compatibilityCacheKey(target, target, 'future-test-policy')).not.toBe(baseline);
  const changed = matchEvidenceSchema.parse(merge(target, { media: { release: { duration: { seconds: 6001 } } } }));
  expect(compatibilityCacheKey(target, changed)).not.toBe(baseline);
  expect(compatibilityCacheKey({ fingerprints: target.fingerprints, media: target.media }, target)).toBe(baseline);
});

it('rejects contradictory sizes, duplicate algorithms/namespaces and invalid data', () => {
  const target = matchEvidenceSchema.parse(fixture.target);
  for (const patch of [
    { media: { release: { size_bytes: 101 } } },
    { fingerprints: [...target.fingerprints, ...target.fingerprints] },
    { media: { identifiers: [...target.media.identifiers, ...target.media.identifiers] } },
    { media: { release: { duration: { seconds: NaN } } } },
  ]) expect(() => matchEvidenceSchema.parse(merge(target, patch))).toThrow();
});

it.each(['number', 'series', 'alternative'])('guards episode %s evidence', (change) => {
  const episodeCase = fixture.cases.find(item => item.name === 'double-special')!;
  const target = matchEvidenceSchema.parse(merge(fixture.target, episodeCase.target!));
  const patch = change === 'series'
    ? { media: { series_identifiers: [{ provider: 'tvdb', entity_type: 'series', value: '8', provenance: 'source_metadata' }] } }
    : { media: { episode_numbers: [{ scheme: change === 'alternative' ? 'dvd' : 'aired', season: 0, number: 3 }], episode_span: [] } };
  const result = classify(target, matchEvidenceSchema.parse(merge(target, patch)));
  if (change === 'series') expect(result.video.conflicts).toContain('series_identity');
  else expect(result.video.band).toBe(change === 'alternative' ? 'title_only' : 'incompatible');
});

it('puts compatibility before public ID and rejects full/sample contradictions', () => {
  const target = matchEvidenceSchema.parse(fixture.target);
  const uncertain = { ...target, fingerprints: [] };
  expect(classifyCandidates(target, [['a', uncertain], ['z', target]], 2).map(([id]) => id)).toEqual(['z', 'a']);
  const full = { algorithm: 'sha256-full-1', digest: 'b'.repeat(64), size_bytes: 100 };
  const first = { ...target, fingerprints: [full, ...target.fingerprints] };
  const second = { ...target, fingerprints: [full, { ...target.fingerprints[0]!, digest: 'c'.repeat(64) }] };
  expect(classify(first, second).video.conflicts).toContain('fingerprint_evidence');
});

it.each(streamFixture.cases)('classifies shared stream $name evidence', async (testCase) => {
  const target = matchEvidenceSchema.parse(merge(merge(fixture.target, streamFixture.target), ('target' in testCase ? testCase.target : {}) ?? {}));
  const candidate = matchEvidenceSchema.parse(merge(target, testCase.candidate));
  const result = classify(target, candidate);
  expect(result.policy_version).toBe(streamFixture.policy_version);
  expect(result.selected).toBe(false);
  const expected = testCase as typeof testCase & Record<string, unknown>;
  for (const name of ['video', 'audio'] as const) {
    expect(result[name].band).toBe(testCase[name]);
    expect(result[name].conflicts).toEqual(expected[`${name}_conflicts`] ?? []);
    expect(result[name].missing).toEqual(expect.arrayContaining(expected[`${name}_missing`] ?? []));
    expect(result[name].strong_eligible).toBe(['exact_file', 'strong_fingerprint'].includes(testCase[name]));
  }
  const bytes = new TextEncoder().encode(JSON.stringify(canonical(result)));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    byte => byte.toString(16).padStart(2, '0')).join('');
  expect(digest).toBe(streamFixture.dto_sha256[testCase.name as keyof typeof streamFixture.dto_sha256]);
});

it('requires matching PCM samples and playback timing for a strong audio match', () => {
  const seed = merge(fixture.target, streamFixture.target);
  const target = matchEvidenceSchema.parse(merge(seed, {
    media: { release: { audio: { language: null, role: 'unknown' } } },
    audio_stream: { algorithm: PCM_AUDIO_ALGORITHM, provenance: 'selected', playback_start: '1/4', duration: '16001/16000' },
  }));
  const candidate = matchEvidenceSchema.parse(merge(target, {
    audio_stream: { provenance: 'analyzed', playback_start: '250/1000' },
  }));
  expect(classify(target, candidate).audio.strong_eligible).toBe(true);
  for (const [field, value] of [['playback_start', '0'], ['duration', '1']] as const) {
    const shifted = matchEvidenceSchema.parse(merge(candidate, { audio_stream: { [field]: value } }));
    expect(classify(target, shifted).audio.conflicts).toContain(`audio_stream_${field}`);
  }
  for (const patch of [{ digest: 'f'.repeat(64) }, { playback_start: null }, { provenance: 'selected' }]) {
    const uncertain = matchEvidenceSchema.parse(merge(candidate, { audio_stream: patch }));
    expect(classify(target, uncertain).audio.strong_eligible).toBe(false);
  }
});

it('bounds rational timing, metadata and channel domains', () => {
  const target = matchEvidenceSchema.parse(merge(fixture.target, streamFixture.target));
  for (const patch of [
    ...['1/0', '1/-2', 'NaN', '1e3', '9223372036854775808', '1/9223372036854775808'].map(playback_start => ({ audio_stream: { playback_start } })),
    { audio_stream: { duration: '0' } }, { audio_stream: { duration: '-1' } },
    { audio_stream: { description: 'x'.repeat(513) } }, { audio_stream: { provenance: 'requested' } },
    { video_stream: target.audio_stream }, { audio_stream: { digest: 'a'.repeat(63) } },
  ]) expect(() => matchEvidenceSchema.parse(merge(target, patch))).toThrow();
  for (const changes of [{ playback_start: '1' }, { provenance: 'unknown' }, { description: 'renamed' }, { digest: 'a'.repeat(64) }]) {
    const changed = matchEvidenceSchema.parse(merge(target, { audio_stream: changes }));
    expect(compatibilityCacheKey(target, changed)).not.toBe(compatibilityCacheKey(target, target));
  }
  const unknown = matchEvidenceSchema.parse(merge(target, { audio_stream: { provenance: 'unknown' } }));
  for (const candidates of [[['a', unknown], ['z', target]], [['z', target], ['a', unknown]]] as [string, typeof target][][])
    expect(classifyCandidates(target, candidates, 2).map(([id]) => id)).toEqual(['z', 'a']);
});

it.each([['playback_start', 'duration', '1'], ['duration', 'playback_start', '5999']])(
  'preserves known %s conflict when %s is unknown', (field, other, value) => {
    const target = matchEvidenceSchema.parse(merge(fixture.target, streamFixture.target));
    const candidate = matchEvidenceSchema.parse(merge(target, { audio_stream: { [field]: value, [other]: null } }));
    const result = classify(target, candidate);
    expect(result.audio.band).toBe('incompatible');
    expect(result.audio.conflicts).toEqual([`audio_stream_${field}`]);
    expect(result.audio.missing).toContain('audio_stream_timing');
    expect(result.video.band).toBe('strong_fingerprint');
  },
);
