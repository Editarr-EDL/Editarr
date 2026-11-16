import { beforeAll, expect, it } from 'vitest';
import { parseArtifact } from '../src/artifact/codec';
import { verifyArtifactSignature } from '../src/artifact/signatures';
import { publishArtifact } from '../src/contributions/publication';
import gzip from './fixtures/artifact-v1-gzip.json';
import { decode } from './helpers/catalog';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { syntheticPublication, signedArtifact } from './helpers/publication-signatures';

beforeAll(setupPublication);

it('publishes freshly consented v1 bytes without invented audio evidence', async () => {
  const { identity } = await publicationCase('ai');
  const artifactBytes = decode(gzip.base64), parsed = await parseArtifact(artifactBytes, limits.artifact, true);
  const artifact = structuredClone(parsed.artifact);
  artifact.sharing_terms = 'community-reuse-1';
  const input = await signedArtifact(artifact);
  await publishArtifact(db, identity, input, limits, now);
  expect(await db.prepare('SELECT artifact_schema, total_events, audio_events, video_events FROM contribution_revisions WHERE revision_id = ?')
    .bind(input.submissionId).first()).toEqual({ artifact_schema: 'community-artifact-1', total_events: 2, audio_events: 1, video_events: 2 });
});

for (const events of [100, 2500]) {
  it(`measures full signed v2 validation for ${events} generated events, not hosted CPU`, async context => {
    const input = await syntheticPublication(events);
    const fixture = await import('../../tests/fixtures/community/donation-artifacts-v2.json');
    const durations: number[] = [];
    for (let iteration = 0; iteration < 5; iteration++) {
      const started = performance.now();
      await verifyArtifactSignature(input.artifactBytes, input.manifestBytes, input.envelopeBytes,
        { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(fixture.default.public_key), status: 'active' }, input.submissionId, input.bounds);
      durations.push(performance.now() - started);
    }
    durations.sort((left, right) => left - right);
    const measurement = { scope: 'local Node wall time; not hosted CPU', events,
      compressedBytes: input.artifactBytes.length, expandedBytes: input.expandedBytes, manifestBytes: input.manifestBytes.length,
      envelopeBytes: input.envelopeBytes.length, medianMs: durations[2], p95Ms: durations[4] };
    Object.assign(context.task.meta, { publicationValidation: measurement });
    await context.annotate(JSON.stringify(measurement), 'local-measurement');
    expect(input.artifactBytes.length).toBeLessThan(limits.artifact.compressedBytes);
  });
}

it('commits a valid publication regardless of the historical contributor storage cap', async () => {
  await db.prepare('UPDATE artifact_storage_policy SET contributor_bytes = 1').run();
  const { identity } = await publicationCase('ai'), input = await syntheticPublication();
  const before = (await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first())!.total;
  try {
    await expect(publishArtifact(db, identity, input, limits, now)).resolves.toMatchObject({ result: { revisionId: input.submissionId } });
    expect((await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first())!.total).toBe(Number(before) + 1);
    expect(await db.prepare('SELECT state FROM contribution_publications WHERE revision_id = ?').bind(input.submissionId).first())
      .toEqual({ state: 'published' });
  } finally { await db.prepare('UPDATE artifact_storage_policy SET contributor_bytes = 7000000').run(); }
});
