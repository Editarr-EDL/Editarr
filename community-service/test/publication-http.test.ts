import { tunnelActor } from '../src/runtime/environment.mjs';
import { beforeAll, expect, it } from 'vitest';
import service from '../src/index';
import { contributorPublication } from '../src/http/publication';
import { signedPost, signedRead, signingKey } from './helpers/key-lifecycle';
import { fixtureKey, syntheticPublication, uploadBody, encode, signManifest } from './helpers/publication-signatures';
import { db, now, limits, setupPublication } from './helpers/publication';
import { verifiedCase } from './helpers/catalog';
import { signedArtifact } from './helpers/publication-signatures';

beforeAll(setupPublication);
const eligible = async () => 'eligible' as const;
const handle = (request: Request, membership = eligible, caps = limits) =>
  contributorPublication(request, db, 'community.example', now, membership, caps);

it('authenticates an upload larger than the normal 8 KiB request cap and reads its owned result', async () => {
  const input = await syntheticPublication(), key = await fixtureKey(), operationId = crypto.randomUUID();
  const body = uploadBody(input) + ' '.repeat(10000);
  const response = await handle(await signedPost(key, '/v1/contributions', body, operationId));
  expect(response.status).toBe(200);
  const result = await response.json();
  expect(await (await handle(await signedRead(key, `/v1/publication-operations/${operationId}`))).json()).toEqual(result);
  expect(JSON.stringify(result)).not.toMatch(/object_key|artifacts\/|discord|privateKey/);
  const other = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('other', 'other-discord', 'Other', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('other-install', 'other', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'other-install', 'Ed25519', ?, 'active', 1, NULL)").bind(other.keyId, other.publicKey),
  ]);
  expect((await handle(await signedRead(other, `/v1/publication-operations/${operationId}`))).status).toBe(404);
  expect((await handle(await signedPost(other, '/v1/contributions', uploadBody(input), crypto.randomUUID()))).status).toBe(400);
});

it('rejects oversized, ineligible, tampered and noncanonical uploads without publication', async () => {
  const input = await syntheticPublication(), key = await fixtureKey(), body = uploadBody(input);
  expect((await handle(await signedPost(key, '/v1/contributions', body, crypto.randomUUID()), eligible, { ...limits, requestBytes: 10 })).status).toBe(413);
  expect((await handle(await signedPost(key, '/v1/contributions', body, crypto.randomUUID()), async () => 'ineligible' as never)).status).toBe(403);
  const request = await signedPost(key, '/v1/contributions', body, crypto.randomUUID());
  expect((await handle(new Request(request.url, { method: 'POST', headers: request.headers, body: body + 'x' }))).status).toBe(401);
  expect((await handle(await signedPost(key, '/v1/contributions?query=1', body, crypto.randomUUID()))).status).toBe(400);
  expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toBeNull();
});

it('rejects genuinely signed false manifest counts instead of trusting an uploader summary', async () => {
  const input = await syntheticPublication(), key = await fixtureKey();
  const manifest = JSON.parse(new TextDecoder().decode(input.manifestBytes)); manifest.summary.total += 1;
  input.manifestBytes = encode(manifest); input.envelopeBytes = await signManifest(input.manifestBytes);
  const result = await handle(await signedPost(key, '/v1/contributions', uploadBody(input), crypto.randomUUID()));
  expect(result.status).toBe(400);
  expect(await result.json()).toEqual({ error: { code: 'invalid_artifact' } });
  expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toBeNull();
});

for (const [name, mutate] of [
  ['Radarr duration', (matching: Record<string, any>) => {
    matching.media.release.duration = { seconds: 10, source: 'radarr_catalog_minutes', precision_seconds: 60 };
  }],
  ['Sonarr identifier', (matching: Record<string, any>) => {
    matching.media.identifiers = matching.media.identifiers.map((item: object) => ({ ...item, provenance: 'sonarr.series' }));
  }],
] as const) {
  it(`refuses a signed upload carrying ${name} provenance and stores nothing`, async () => {
    const original = await verifiedCase('ai');
    const artifact = structuredClone(original.artifact.artifact);
    if (artifact.schema_version !== 'community-artifact-2') throw new Error('Expected v2');
    artifact.submission_id = crypto.randomUUID();
    artifact.sharing_terms = 'community-reuse-1';
    mutate(artifact.matching);
    const input = await signedArtifact(artifact, true), key = await fixtureKey(), operationId = crypto.randomUUID();
    const payloads = await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first();
    const response = await handle(await signedPost(key, '/v1/contributions', uploadBody(input), operationId));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: { code: 'invalid_artifact' } });
    expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toBeNull();
    expect(await db.prepare('SELECT 1 FROM operations WHERE operation_id = ?').bind(operationId).first()).toBeNull();
    expect(await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first()).toEqual(payloads);
  });
}

it('allows new uploads and owned reads regardless of observed daily activity', async () => {
  const input = await syntheticPublication(), key = await fixtureKey(), operationId = crypto.randomUUID();
  expect((await handle(await signedPost(key, '/v1/contributions', uploadBody(input), operationId))).status).toBe(200);
  await db.prepare("INSERT INTO contributors VALUES ('pressure', 'pressure-discord', 'Pressure', 'active', 1)").run();
  await db.prepare(`INSERT INTO usage_observations VALUES ('pressure', 'd1_writes', '2023-11-14', 'allowed', 1, 80000, ?)` ).bind(now * 1000).run();
  try {
    expect((await handle(await signedPost(key, '/v1/contributions', uploadBody(await syntheticPublication()), crypto.randomUUID()))).status).toBe(200);
    expect((await handle(await signedRead(key, `/v1/publication-operations/${operationId}`))).status).toBe(200);
    await db.prepare("UPDATE usage_observations SET units = 900000 WHERE contributor_id = 'pressure'").run();
    expect((await handle(await signedRead(key, `/v1/publication-operations/${operationId}`))).status).toBe(200);
  } finally { await db.prepare("DELETE FROM usage_observations WHERE contributor_id = 'pressure'").run(); }
});

it('keeps production and unconfigured local publication disabled', async () => {
  const request = new Request('https://community.example/v1/contributions', { method: 'POST' });
  expect((await service.fetch(request, { APP_ENV: 'production' })).status).toBe(503);
  expect((await service.fetch(request, { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor })).status).toBe(503);
});

it('observes a malformed envelope after valid request authentication', async () => {
  const key = await fixtureKey();
  const before = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  expect((await handle(await signedPost(key, '/v1/contributions', '{', crypto.randomUUID()))).status).toBe(400);
  const after = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  expect(after!.used).toBe((before?.used ?? 0) + 1);
});

for (const terms of [null, 'community-reuse-2', 'CC-BY-4.0']) {
  it(`rejects signed unsupported permission ${terms} without storing publication evidence`, async () => {
    const original = await verifiedCase('manual');
    const artifact = structuredClone(original.artifact.artifact);
    artifact.submission_id = crypto.randomUUID();
    artifact.sharing_terms = terms;
    const input = await signedArtifact(artifact), key = await fixtureKey(), operationId = crypto.randomUUID();
    const response = await handle(await signedPost(key, '/v1/contributions', uploadBody(input), operationId));
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: { code: 'sharing_permission_required' } });
    expect(await db.prepare('SELECT 1 FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first()).toBeNull();
    expect(await db.prepare('SELECT 1 FROM operations WHERE operation_id = ?').bind(operationId).first()).toBeNull();
  });
}

it('does not expose contributor withdrawal or deletion endpoints', async () => {
  const input = await syntheticPublication(), key = await fixtureKey(), operationId = crypto.randomUUID();
  expect((await handle(await signedPost(key, '/v1/contributions', uploadBody(input), operationId))).status).toBe(200);
  const before = await db.prepare('SELECT * FROM artifact_payloads').all();
  const state = await db.prepare('SELECT state FROM contribution_publications WHERE revision_id = ?').bind(input.submissionId).first();
  for (const path of [`/v1/contributions/${input.submissionId}`, `/v1/contributions/${input.submissionId}/withdraw`]) {
    const request = await signedPost(key, path, '{}', crypto.randomUUID());
    expect((await service.fetch(request, { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db })).status).toBe(404);
    expect((await service.fetch(new Request(request.url, { method: 'DELETE', headers: request.headers }),
      { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db })).status).toBe(404);
  }
  expect((await db.prepare('SELECT * FROM artifact_payloads').all()).results).toEqual(before.results);
  expect(await db.prepare('SELECT state FROM contribution_publications WHERE revision_id = ?').bind(input.submissionId).first()).toEqual(state);
});