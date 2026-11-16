import { describe, expect, it } from 'vitest';

import { parseArtifact } from '../src/artifact/codec';
import { ArtifactSignatureError, verifyArtifactSignature, type HistoricalKey } from '../src/artifact/signatures';
import gzipFixture from './fixtures/artifact-v1-gzip.json';
import signatures from './fixtures/artifact-signatures-v1.json';

const bounds = { compressedBytes: 4096, expandedBytes: 16384, events: 10, text: 256, depth: 12 };
const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));
const decode = (value: string) => Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
const hex = async (bytes: Uint8Array) => Array.from(new Uint8Array(
  await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
  (byte) => byte.toString(16).padStart(2, '0')).join('');

async function vector(name: 'original' | 'derived') {
  const expected = signatures[name];
  const artifactBytes = decode(name === 'original' ? gzipFixture.base64 :
    signatures.derived.artifact_base64_chunks.join(''));
  expect(await hex(artifactBytes)).toBe(expected.artifact_sha256);
  const parsed = await parseArtifact(artifactBytes, bounds, true);
  const manifestBytes = encode({
    schema_version: 'community-manifest-1', artifact_format: 'gzip-json',
    artifact_schema_version: 'community-artifact-1', artifact_digest: expected.artifact_sha256,
    submission_id: parsed.artifact.submission_id,
    contributor_id: parsed.artifact.author.contributor_id, key_id: parsed.artifact.author.key_id,
    summary: parsed.summary,
  });
  expect(await hex(manifestBytes)).toBe(expected.manifest_sha256);
  const envelopeBytes = encode({ schema_version: 'community-artifact-signature-1',
    signature: expected.signature_base64 });
  const key: HistoricalKey = {
    contributorId: parsed.artifact.author.contributor_id, keyId: parsed.artifact.author.key_id,
    publicKey: decode(expected.public_key_base64), status: 'active',
  };
  return { artifactBytes, manifestBytes, envelopeBytes, key,
    submissionId: parsed.artifact.submission_id };
}

describe('durable artifact signatures v1', () => {
  it.each(['original', 'derived'] as const)('verifies Python-signed %s bytes and preserves them', async (name) => {
    const input = await vector(name);
    const verified = await verifyArtifactSignature(input.artifactBytes, input.manifestBytes,
      input.envelopeBytes, input.key, input.submissionId, bounds);
    expect(verified.artifactBytes).toBe(input.artifactBytes);
    expect(verified.manifestBytes).toBe(input.manifestBytes);
    expect(verified.envelopeBytes).toBe(input.envelopeBytes);
    if (name === 'derived') {
      expect(verified.artifact.artifact.source_references?.[0]).toEqual({
        submission_id: 'submission-1', digest: signatures.original.artifact_sha256,
      });
      expect(verified.contributorId).toBe('contributor-2');
    }
  });

  it('rejects changed original bytes, author/key/submission and compromised history', async () => {
    const input = await vector('original');
    const verify = (change: Partial<typeof input>) => verifyArtifactSignature(
      change.artifactBytes ?? input.artifactBytes,
      change.manifestBytes ?? input.manifestBytes,
      change.envelopeBytes ?? input.envelopeBytes,
      change.key ?? input.key, change.submissionId ?? input.submissionId, bounds);
    const whitespace = new TextDecoder().decode(input.manifestBytes).replace(
      '"submission_id"', ' "submission_id"');
    await expect(verify({ manifestBytes: new TextEncoder().encode(whitespace) }))
      .rejects.toThrow(ArtifactSignatureError);
    const changedPayload = Uint8Array.from(input.artifactBytes);
    changedPayload[changedPayload.length - 1] ^= 1;
    await expect(verify({ artifactBytes: changedPayload })).rejects.toThrow();
    await expect(verify({ key: { ...input.key, contributorId: 'other' } }))
      .rejects.toThrow(ArtifactSignatureError);
    await expect(verify({ key: { ...input.key, keyId: 'other' } }))
      .rejects.toThrow(ArtifactSignatureError);
    await expect(verify({ submissionId: 'other' })).rejects.toThrow(ArtifactSignatureError);
    await expect(verify({ key: { ...input.key, publicKey: decode(signatures.derived.public_key_base64) } }))
      .rejects.toThrow(ArtifactSignatureError);
    for (const status of ['compromised', 'revoked'] as const) {
      await expect(verify({ key: { ...input.key, status } })).rejects.toThrow(ArtifactSignatureError);
    }
    await expect(verify({ key: { ...input.key, status: 'retired' } }))
      .resolves.toMatchObject({ keyStatus: 'retired' });
    const changedEnvelope = new TextDecoder().decode(input.envelopeBytes)
      .replace('"signature":', '"signature":"duplicate","signature":');
    await expect(verify({ envelopeBytes: new TextEncoder().encode(changedEnvelope) }))
      .rejects.toThrow(ArtifactSignatureError);
    const badSignature = new TextDecoder().decode(input.envelopeBytes)
      .replace(signatures.original.signature_base64[0], 'A');
    await expect(verify({ envelopeBytes: new TextEncoder().encode(badSignature) }))
      .rejects.toThrow(ArtifactSignatureError);
    const unknownVersion = new TextDecoder().decode(input.envelopeBytes)
      .replace('community-artifact-signature-1', 'community-artifact-signature-2');
    await expect(verify({ envelopeBytes: new TextEncoder().encode(unknownVersion) }))
      .rejects.toThrow(ArtifactSignatureError);
  });

  it('verifies original stored bytes after an independent download serialization round trip', async () => {
    const input = await vector('derived');
    const stored = JSON.stringify({ payload: btoa(String.fromCharCode(...input.artifactBytes)),
      manifest: btoa(String.fromCharCode(...input.manifestBytes)),
      envelope: btoa(String.fromCharCode(...input.envelopeBytes)) });
    const restored = JSON.parse(stored) as Record<string, string>;
    const verified = await verifyArtifactSignature(decode(restored.payload), decode(restored.manifest),
      decode(restored.envelope), { ...input.key, status: 'retired' }, input.submissionId, bounds);
    expect(verified.keyStatus).toBe('retired');
    expect(verified.manifestBytes).toEqual(input.manifestBytes);
    expect(verified.artifactBytes).toEqual(input.artifactBytes);
    expect(verified.envelopeBytes).toEqual(input.envelopeBytes);
  });
});