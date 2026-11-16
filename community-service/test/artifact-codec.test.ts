import { describe, expect, it } from 'vitest';

import { ArtifactError, parseArtifact, parseManifest } from '../src/artifact/codec';
import fixture from './fixtures/artifact-v1.json';
import gzipVector from './fixtures/artifact-v1-gzip.json';

const bounds = { compressedBytes: 4096, expandedBytes: 16384, events: 10, text: 256, depth: 12 };
const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));
const copy = () => structuredClone(fixture) as Record<string, any>;

describe('community artifact v1', () => {
  it('preserves exact bytes, unknown provenance, overlapping channels and unclassified events', async () => {
    const raw = bytes(fixture);
    const digest = await crypto.subtle.digest('SHA-256', raw as BufferSource);
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
    expect(raw.byteLength).toBe(1023);
    expect(hash).toBe('1b23acc6ba8722de0e3a518437ed3f5ea770045aa40f10b80d44e84e07ad7c21');
    const parsed = await parseArtifact(raw, bounds);
    expect(parsed.storedBytes).toBe(raw);
    expect(parsed.summary.total).toBe(2);
    expect(parsed.summary.channels).toEqual({ audio: 1, video: 2 });
    expect(parsed.summary.taxonomy).toEqual({ 'language/swearing': 1, unclassified: 1 });
    expect(parsed.summary.coverage[0].state).toBe('unknown');
    expect(parsed.summary.work_ids).toEqual([{ provider: 'tmdb', entity_type: 'movie', value: '123' }]);
  });

  it('accepts manual and derived artifacts with explicit lineage', async () => {
    const manual = copy();
    manual.coverage[0].state = 'partial';
    manual.coverage[0].intervals = [{ start_seconds: 1, end_seconds: 5 }];
    manual.provenance.stages = [{ execution_id: 'attempt-1', capability: 'language',
      state: 'partial', model_id: null, model_revision: null, model_digest: null,
      settings: { audio_model: 'whisper' }, intervals: [{ start_seconds: 1, end_seconds: 5 }] }];
    expect((await parseArtifact(bytes(manual), bounds)).summary.coverage[0].state).toBe('partial');
    manual.provenance.stages = [];
    manual.kind = 'manual';
    manual.findings = [manual.findings[1]];
    expect((await parseArtifact(bytes(manual), bounds)).summary.total).toBe(1);
    manual.kind = 'derived';
    manual.media.timeline = 'derived_target';
    manual.findings[0].source = 'derived';
    manual.findings[0].parent_event_id = 'parent-event';
    manual.derivation = { parents: [{ submission_id: 'parent', digest: 'a'.repeat(64) }],
      transform: 'retimed' };
    expect((await parseArtifact(bytes(manual), bounds)).summary.sources).toEqual({ derived: 1 });
  });

  it.each([
    (item: Record<string, any>) => { item.schema_version = 'community-artifact-2'; },
    (item: Record<string, any>) => { item.findings[0].confidence = NaN; },
    (item: Record<string, any>) => { item.findings[0].subcategory = 'murder'; },
    (item: Record<string, any>) => { item.findings[0].end_seconds = 13; },
    (item: Record<string, any>) => { item.findings[1].event_id = 'event-1'; },
    (item: Record<string, any>) => { item.coverage[0].intervals = [{ start_seconds: 4, end_seconds: 15 }]; },
    (item: Record<string, any>) => { item.provenance.stages = [{ execution_id: 'attempt-1',
      capability: 'audio', state: 'partial', model_id: null, model_revision: null,
      model_digest: null, settings: { secret: 'not public' }, intervals: [] }]; },
  ])('rejects an invalid or future artifact', async (mutate) => {
    const item = copy();
    mutate(item);
    if (Number.isNaN(item.findings[0].confidence)) {
      const raw = bytes(fixture);
      await expect(parseArtifact(new TextEncoder().encode(new TextDecoder().decode(raw)
        .replace('"confidence":null', '"confidence":NaN')), bounds)).rejects.toThrow(ArtifactError);
    } else {
      await expect(parseArtifact(bytes(item), bounds)).rejects.toThrow(ArtifactError);
    }
  });

  it('rejects duplicate fields and over-expanded gzip', async () => {
    const raw = bytes(fixture);
    const duplicate = new TextDecoder().decode(raw).replace('"submission_id":"submission-1",',
      '"submission_id":"submission-1","submission_id":"other",');
    await expect(parseArtifact(new TextEncoder().encode(duplicate), bounds)).rejects.toThrow(/Duplicate/);
    const gzip = new Blob([raw as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'));
    const compressed = new Uint8Array(await new Response(gzip).arrayBuffer());
    expect((await parseArtifact(compressed, bounds, true)).storedBytes).toBe(compressed);
    await expect(parseArtifact(compressed, { ...bounds, expandedBytes: 100 }, true))
      .rejects.toThrow(/Expanded/);
  });

  it('binds exact compressed bytes, identity and recomputed summary in the manifest', async () => {
    const raw = bytes(fixture);
    const gzip = new Blob([raw as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'));
    const compressed = new Uint8Array(await new Response(gzip).arrayBuffer());
    const artifact = await parseArtifact(compressed, bounds, true);
    const digest = await crypto.subtle.digest('SHA-256', compressed as BufferSource);
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
    const manifest = { schema_version: 'community-manifest-1', artifact_format: 'gzip-json',
      artifact_schema_version: 'community-artifact-1', artifact_digest: hash,
      submission_id: 'submission-1', contributor_id: 'contributor-1', key_id: 'key-1',
      summary: artifact.summary };
    const manifestBytes = bytes(manifest);
    expect((await parseManifest(manifestBytes, artifact, bounds)).storedBytes).toBe(manifestBytes);
    for (const change of [{ artifact_digest: '0'.repeat(64) }, { contributor_id: 'other' },
      { summary: { ...artifact.summary, total: 200 } },
      { summary: { ...artifact.summary, channels: { audio: true, video: 2 } } }]) {
      await expect(parseManifest(bytes({ ...manifest, ...change }), artifact, bounds))
        .rejects.toThrow(/does not match/);
    }
    await expect(parseManifest(manifestBytes, await parseArtifact(raw, bounds), bounds))
      .rejects.toThrow(/requires gzip/);
  });

  it('decodes the identical Python gzip bytes and rejects overlong text and transcripts', async () => {
    const compressed = Uint8Array.from(atob(gzipVector.base64), (char) => char.charCodeAt(0));
    const digest = await crypto.subtle.digest('SHA-256', compressed as BufferSource);
    expect(Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''))
      .toBe(gzipVector.sha256);
    expect((await parseArtifact(compressed, bounds, true)).summary.total).toBe(2);
    const note = copy();
    note.findings[0].note = 'x'.repeat(257);
    await expect(parseArtifact(bytes(note), bounds)).rejects.toThrow(/text/);
    const transcript = copy();
    transcript.findings[0].transcript = 'private text';
    await expect(parseArtifact(bytes(transcript), bounds)).rejects.toThrow(ArtifactError);
    await expect(parseArtifact(bytes(fixture), { ...bounds, depth: 2 })).rejects.toThrow(/depth/);
  });
});