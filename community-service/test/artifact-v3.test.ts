import { gzipSync } from 'node:zlib';
import { describe, expect, it } from 'vitest';
import fixture from './fixtures/community-v3/artifact-v3.json';
import { ArtifactV3Error, decodeArtifactV3, parseManifestV3 } from '../src/artifact/v3';
import { readEnvelope, signatureBase, verifySubmission } from '../src/artifact/signatures';
import { gunzipSingleMember, parseStrictJson } from '../src/artifact/strict-json';

const bytes = (value: string) => Uint8Array.from(atob(value), character => character.charCodeAt(0));
const text = (value: string) => new TextEncoder().encode(value);
const signer = { contributorId: 'c_7f3a', keyId: fixture.authorKeyId, publicKey: bytes(fixture.authorPublicKeyBase64) };
const gzip = (value: unknown) => new Uint8Array(gzipSync(text(typeof value === 'string' ? value : JSON.stringify(value))));
const code = async (promise: Promise<unknown> | (() => unknown)) => {
  try { await (typeof promise === 'function' ? promise() : promise); return null; }
  catch (error) { return error instanceof ArtifactV3Error ? error.code : String(error); }
};
const artifact = (changes: Record<string, unknown> = {}) => ({ ...fixture.artifact, ...changes });
const entry = (changes: Record<string, unknown>) => artifact({ entries: [{ ...fixture.artifact.entries[0], ...changes }] });

describe('community-artifact-3 cross-language vectors (§5)', () => {
  it('verifies the shared fixture bytes, digest, signature base and envelope exactly as given', async () => {
    const verified = await verifySubmission(bytes(fixture.gzipBase64), text(fixture.manifest), text(fixture.envelope), signer);
    expect(verified.decoded.digest).toBe(fixture.artifactDigest);
    expect(verified.decoded.artifact).toEqual(fixture.artifact);
    expect(JSON.stringify(verified.decoded.artifact)).toBe(fixture.canonicalJson);
    expect(Buffer.from(await signatureBase(text(fixture.manifest), verified.manifest)).toString('hex')).toBe(fixture.signatureBaseHex);
    expect(readEnvelope(text(fixture.envelope))).toHaveLength(64);
  });

  it('refuses a different signer, tampered bytes and a noncanonical envelope', async () => {
    expect(await code(verifySubmission(bytes(fixture.gzipBase64), text(fixture.manifest), text(fixture.envelope),
      { ...signer, contributorId: 'c_other' }))).toBe('key_mismatch');
    const manifest = fixture.manifest.replace('"summary":{}', '"summary":{"x":1}');
    expect(await code(verifySubmission(bytes(fixture.gzipBase64), text(manifest), text(fixture.envelope), signer))).toBe('invalid_submission');
    const envelope = JSON.parse(fixture.envelope) as { signature: string };
    const flipped = JSON.stringify({ schema_version: 'community-artifact-signature-1',
      signature: (envelope.signature[0] === 'A' ? 'B' : 'A') + envelope.signature.slice(1) });
    expect(await code(verifySubmission(bytes(fixture.gzipBase64), text(fixture.manifest), text(flipped), signer))).toBe('invalid_submission');
    expect(await code(() => readEnvelope(text(fixture.envelope.replace('{"', '{ "'))))).toBe('invalid_submission');
  });
});

describe('strict JSON and gzip bounds (§5.1, §5.2)', () => {
  it('rejects BOM, duplicates, depth, long strings, fractions outside scale and unsafe integers', () => {
    const fail = (source: string, bounds = {}) => expect(() => parseStrictJson(text(source), { maxBytes: 4096, ...bounds })).toThrow();
    fail('\ufeff{}');
    fail('{"a":1,"a":2}');
    fail('{"a":{"b":{"c":[]}}}', { maxDepth: 3 });
    fail(`{"a":"${'x'.repeat(5)}"}`, { maxStringBytes: 4 });
    fail('{"startMs":1.0}');
    fail('{"startMs":1e3}');
    fail('{"n":9007199254740992}');
    expect(parseStrictJson(text('{"alignmentHint":{"scale":1.05}}'), { maxBytes: 99, fractionPaths: ['alignmentHint.scale'] }))
      .toEqual({ alignmentHint: { scale: 1.05 } });
    expect(parseStrictJson(text('{"a":{"b":[1]}}'), { maxBytes: 99, maxDepth: 3 })).toEqual({ a: { b: [1] } });
  });

  it('expands only one plain gzip member within limits', async () => {
    const plain = gzip({ a: 1 });
    expect(new TextDecoder().decode(await gunzipSingleMember(plain, 1000, 1000))).toBe('{"a":1}');
    const named = Uint8Array.from(plain); named[3] = 0x08;
    await expect(gunzipSingleMember(named, 1000, 1000)).rejects.toThrow();
    await expect(gunzipSingleMember(new Uint8Array([...plain, ...plain]), 1000, 1000)).rejects.toThrow();
    await expect(gunzipSingleMember(plain, plain.length - 1, 1000)).rejects.toThrow();
    await expect(gunzipSingleMember(plain, 1000, 3)).rejects.toThrow();
    const corrupt = Uint8Array.from(plain); corrupt[corrupt.length - 5] ^= 1;
    await expect(gunzipSingleMember(corrupt, 1000, 1000)).rejects.toThrow();
  });
});

describe('artifact validation codes (§5.2, §7.2)', () => {
  it.each([
    ['unknown field', artifact({ extra: 1 }), 'invalid_submission'],
    ['missing provider identity', artifact({ work: { ...fixture.artifact.work, tmdb: null } }), 'work_identity_required'],
    ['movie numbering', artifact({ work: { ...fixture.artifact.work, season: 1 } }), 'invalid_submission'],
    ['blank normalized title', artifact({ work: { ...fixture.artifact.work, title: '!!' } }), 'invalid_submission'],
    ['duplicate key type', artifact({ media: { ...fixture.artifact.media, matchKeys: [fixture.artifact.media.matchKeys[1],
      { ...fixture.artifact.media.matchKeys[1], value: 'b'.repeat(32) }] } }), 'invalid_submission'],
    ['unequal key sizes', artifact({ media: { ...fixture.artifact.media, matchKeys: [fixture.artifact.media.matchKeys[0],
      { ...fixture.artifact.media.matchKeys[1], sizeBytes: 1 }] } }), 'invalid_submission'],
    ['unknown category', entry({ category: 'weather' }), 'invalid_taxonomy'],
    ['foreign subcategory', entry({ subcategory: 'swearing' }), 'invalid_taxonomy'],
    ['coverage category', artifact({ coverage: [{ category: 'weather', intervals: [{ startMs: 0, endMs: 1 }] }] }), 'invalid_taxonomy'],
    ['entry past duration', entry({ endMs: 8_160_513 }), 'invalid_bounds'],
    ['empty entry', entry({ endMs: 600_000 }), 'invalid_bounds'],
    ['overlapping coverage', artifact({ coverage: [{ category: 'violence', intervals: [{ startMs: 0, endMs: 10 }, { startMs: 5, endMs: 20 }] }] }), 'invalid_bounds'],
    ['dispute without proposal', artifact({ entries: [fixture.artifact.entries[1]].map(item => ({ ...item, proposedFilterId: null })) }), 'invalid_submission'],
    ['classified dispute', artifact({ entries: [{ ...fixture.artifact.entries[1], severity: 'low' }] }), 'invalid_submission'],
    ['model without version', entry({ evidence: { ...fixture.artifact.entries[0].evidence, origin: 'model' } }), 'invalid_submission'],
    ['integer written as fraction', null, 'invalid_submission'],
  ])('%s', async (_name, value, expected) => {
    const source = value === null ? fixture.canonicalJson.replace('"durationMs":8160512', '"durationMs":8160512.0') : JSON.stringify(value);
    expect(await code(decodeArtifactV3(gzip(source)))).toBe(expected);
  });

  it('binds the manifest to the exact gzip bytes and artifact identity', async () => {
    const decoded = await decodeArtifactV3(bytes(fixture.gzipBase64));
    expect(parseManifestV3(text(fixture.manifest), decoded).submission_id).toBe(fixture.artifact.submissionId);
    for (const [from, to] of [['"submission_id":"sub_01J9Z3K5C6Q"', '"submission_id":"sub_other"'], ['"summary":{}', '"summary":{},"x":1'],
      [fixture.artifactDigest, 'f'.repeat(64)]])
      expect(await code(() => parseManifestV3(text(fixture.manifest.replace(from, to)), decoded))).toBe('invalid_submission');
  });
});
