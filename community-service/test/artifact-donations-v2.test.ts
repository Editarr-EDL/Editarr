import { describe, expect, it } from 'vitest';

import { ArtifactError, parseArtifact, parseManifest } from '../src/artifact/codec';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { sha256 } from '../src/catalog/records';
import { validateParentPermission } from '../src/contributions/permission';
import fixture from '../../tests/fixtures/community/donation-artifacts-v2.json';
import filterDetails from '../../tests/fixtures/community/donation-artifact-filter-details.json';
import { signManifest, signedArtifact } from './helpers/publication-signatures';

const bounds = { compressedBytes: 65536, expandedBytes: 524288, events: 100, text: 4096, depth: 24 };
const decode = (value: string) => Uint8Array.from(atob(value), char => char.charCodeAt(0));
const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));
const names = Object.keys(fixture.cases) as (keyof typeof fixture.cases)[];

describe('Python donation artifact v2', () => {
  it.each(names)('verifies original %s bytes, summary and lineage', async name => {
    const item = fixture.cases[name];
    const artifactBytes = decode(item.artifact), manifestBytes = decode(item.manifest), envelopeBytes = decode(item.envelope);
    const verified = await verifyArtifactSignature(artifactBytes, manifestBytes, envelopeBytes,
      { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(fixture.public_key), status: 'active' },
      `submission-${name}`, bounds);
    expect(verified.artifactBytes).toBe(artifactBytes);
    expect(verified.manifestBytes).toBe(manifestBytes);
    expect(verified.envelopeBytes).toBe(envelopeBytes);
    expect(verified.artifact.summary).toEqual(JSON.parse(new TextDecoder().decode(manifestBytes)).summary);
    expect(verified.artifact.summary).not.toHaveProperty('manual_filter_counts');
    const artifact = verified.artifact.artifact;
    expect(artifact.schema_version).toBe('community-artifact-2');
    if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2');
    if (name === 'zero') expect(artifact.findings).toHaveLength(0);
    if (name === 'legacy') expect(artifact.audio_association).toBe('unknown');
    if (name === 'partial') expect(artifact.sources[0].status).toBe('failed');
    if (name === 'manual' || name === 'imported') expect(artifact.kind).toBe('manual');
    if (name === 'imported') {
      expect(artifact.sources[0].author_id).toBe('original-author');
      expect(artifact.source_references?.[0].submission_id).toBe('original-revision');
    }
    if (name === 'derived' || name === 'retimed') {
      expect(artifact.kind).toBe('derived');
      expect(artifact.findings.map(item => item.start_seconds)).toEqual([4, 4]);
      expect(artifact.retiming.every(item => item.applied)).toBe(true);
      expect(artifact.sources).toHaveLength(2);
    }
    const publicBytes = JSON.stringify(artifact);
    for (const canary of ['PRIVATE', '/private/', 'private-provider', 'stream_index', 'file_version'])
      expect(publicBytes).not.toContain(canary);
  });

  it('verifies tracked counts and deduplicated models from a signed Python review', async () => {
    const item = filterDetails.cases.tracked;
    const verified = await verifyArtifactSignature(decode(item.artifact), decode(item.manifest), decode(item.envelope),
      { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(filterDetails.public_key), status: 'active' },
      'submission-tracked', bounds);
    expect(verified.artifact.summary).toMatchObject({
      manual_filter_counts: { added: 1, edited: 1 },
      models: [
        { capability: 'audio_filter', model_id: 'alt_profanity_check', model_revision: null },
        { capability: 'audio_scan', model_id: 'whisper', model_revision: 'a'.repeat(40) },
        { capability: 'visual_qwen', model_id: 'qwen3-vl-8b', model_revision: 'a'.repeat(40) },
      ],
    });
    const artifact = verified.artifact.artifact;
    if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected tracked v2');
    const missingFlag = structuredClone(artifact);
    delete missingFlag.findings[0].manual;
    await expect(parseArtifact(encode(missingFlag), bounds)).rejects.toThrow(ArtifactError);
    const missingVersion = structuredClone(artifact);
    delete missingVersion.manual_filter_tracking;
    await expect(parseArtifact(encode(missingVersion), bounds)).rejects.toThrow(ArtifactError);
  });

  it.each([
    (item: Record<string, any>) => { item.source_revision = -1; },
    (item: Record<string, any>) => { item.audio_association = 'unknown'; },
    (item: Record<string, any>) => { item.sources[0].status = 'running'; },
    (item: Record<string, any>) => { item.sources.push(structuredClone(item.sources[0])); },
    (item: Record<string, any>) => { item.sources[0].origin = 'manual'; },
    (item: Record<string, any>) => { item.analyzed_audio.language = 'fr'; },
    (item: Record<string, any>) => { item.analyzed_audio.stream_index = 1; },
    (item: Record<string, any>) => { item.findings[0].source_snapshot_id = 'missing'; },
    (item: Record<string, any>) => { item.sources[0].provenance.stages[0].settings.api_key = 'private'; },
  ])('rejects malformed v2 claims independently', async mutate => {
    const parsed = await parseArtifact(decode(fixture.cases.ai.artifact), bounds, true);
    const item = structuredClone(parsed.artifact);
    mutate(item);
    await expect(parseArtifact(encode(item), bounds)).rejects.toThrow(ArtifactError);
  });

  it('rejects reapplied retiming and manifest schema/summary substitution', async () => {
    const parsed = await parseArtifact(decode(fixture.cases.retimed.artifact), bounds, true);
    const item = structuredClone(parsed.artifact) as Record<string, any>;
    item.retiming[0].applied = false;
    await expect(parseArtifact(encode(item), bounds)).rejects.toThrow(ArtifactError);
    const manifest = JSON.parse(new TextDecoder().decode(decode(fixture.cases.retimed.manifest)));
    await expect(parseManifest(encode({ ...manifest, artifact_schema_version: 'community-artifact-1' }), parsed, bounds)).rejects.toThrow(ArtifactError);
    await expect(parseManifest(encode({ ...manifest, summary: { ...manifest.summary, source_revision: 99 } }), parsed, bounds)).rejects.toThrow(ArtifactError);
  });
});
describe('artifacts signed before migration 0017', () => {
  const legacy = async () => {
    const parsed = await parseArtifact(decode(fixture.cases.legacy.artifact), bounds, true);
    const artifact = structuredClone(parsed.artifact) as Record<string, any>;
    artifact.sharing_terms = 'community-reuse-1';
    artifact.matching.media.identifiers = artifact.matching.media.identifiers.map((item: object) => ({ ...item, provenance: 'radarr.movie' }));
    artifact.matching.media.release.duration = { seconds: artifact.media.duration_seconds, source: 'radarr_catalog_minutes', precision_seconds: 60 };
    const { artifactBytes, manifestBytes } = await signedArtifact(artifact as never, true);
    const manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
    manifest.summary.matching = artifact.matching;
    const signedManifest = encode(manifest);
    return { artifact, artifactBytes, manifestBytes: signedManifest, envelopeBytes: await signManifest(signedManifest) };
  };

  it('refuses Arr provenance in new bytes and reads stored bytes the way 0017 rewrote the catalog', async () => {
    const { artifact, artifactBytes, manifestBytes, envelopeBytes } = await legacy();
    await expect(parseArtifact(artifactBytes, bounds, true)).rejects.toThrow(ArtifactError);
    const key = { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(fixture.public_key), status: 'retired' } as const;
    await expect(verifyArtifactSignature(artifactBytes, manifestBytes, envelopeBytes, key, artifact.submission_id, bounds))
      .rejects.toThrow(ArtifactError);
    const verified = await verifyArtifactSignature(artifactBytes, manifestBytes, envelopeBytes, key, artifact.submission_id, bounds, true);
    if (verified.artifact.artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2');
    expect(verified.artifactBytes).toBe(artifactBytes);
    expect(verified.artifact.artifact.matching.media.identifiers.map(item => item.provenance)).toEqual(['source_metadata']);
    expect(verified.artifact.artifact.matching.media.release.duration)
      .toEqual({ seconds: artifact.media.duration_seconds, source: 'jellyfin_ticks', precision_seconds: 60 });
  });

  it('keeps a stored Arr-provenance parent eligible for derived publications', async () => {
    const { artifact, artifactBytes } = await legacy();
    const child = (await parseArtifact(decode(fixture.cases.ai.artifact), bounds, true)).artifact;
    await expect(validateParentPermission(child, { revision_id: artifact.submission_id, contributor_id: artifact.author.contributor_id,
      artifact_digest: await sha256(artifactBytes), artifact: artifactBytes.slice().buffer }, bounds)).resolves.toBeUndefined();
  });
});
