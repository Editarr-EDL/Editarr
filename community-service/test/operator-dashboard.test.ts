import { tunnelActor } from '../src/runtime/environment.mjs';
import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, generateKeyPair, exportJWK, SignJWT, type JWTVerifyGetKey } from 'jose';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import download from '../migrations/0011_download_usage.sql?raw';
import { db, now, setupPublication, publicationCase, limits } from './helpers/publication';
import { publishArtifact } from '../src/contributions/publication';
import { operatorService } from '../src/admin/service';
import { observeDownloadDatabase } from '../src/budgets/download-usage';

const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64}) };
let jwt: string, resolver: JWTVerifyGetKey;
beforeAll(async () => {
  await setupPublication();
  for (const sql of [download, imports, votes, moderation]) await db.exec(sql.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  await publishArtifact(db, item.identity, item.input, limits, now);
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});
function request(path: string, token = jwt) {
  return new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/dashboard/${path}`, { headers: { 'Cf-Access-Jwt-Assertion': token } });
}
const handle = (path: string) => operatorService(request(path), settings, resolver, new Date(now * 1000));

it('protects every read and never sends deployment configuration, Discord IDs, payloads or operation bodies', async () => {
  for (const path of ['overview', 'contributions', 'contributors', 'contributors/contributor-1/keys',
    'contributors/contributor-1/usage', 'operations', 'audit']) {
    expect((await operatorService(request(path, ''), settings, resolver, new Date(now * 1000))).status).toBe(401);
    const response = await handle(path);
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
    const body = await response.text();
    for (const forbidden of ['discord_user_id', 'public_key', 'request_digest', 'summary_json', 'ACCESS_', 'artifactBytes', 'result'])
      expect(body).not.toContain(`"${forbidden}`);
  }
});

it('keeps payload storage, provider size, daily overlapping observations and backups separate with unknowns', async () => {
  await db.prepare(`INSERT INTO download_usage_observations VALUES ('contributor-1', ?, 'allowed', 2, 17, 1, 8, 0, ?)`)
    .bind(new Date(now * 1000).toISOString().slice(0, 10), now * 1000 - 1000).run();
  const value = await (await handle('overview')).json() as Record<string, any>;
  expect(value.storage).toMatchObject({ source: 'artifact_storage_usage', providerVerified: false });
  expect(value.storage).not.toHaveProperty('globalLimitBytes');
  expect(value.storage.globalBytes).toBeGreaterThan(0);
  for (const key of ['databaseSize', 'providerUsage', 'backup']) expect(value[key]).toEqual({ source: 'unavailable', observedAt: null, value: null, providerVerified: false });
  expect(value.observations[0]).toMatchObject({ reads: 17, unknownReads: 1, writes: 8, providerVerified: false });
  expect(value.observations[1]).toMatchObject({ reads: null, observedAt: null });
  expect(value).not.toHaveProperty('totalUsage');
  const usage = await (await handle('contributors/contributor-1/usage')).json() as Record<string, any>;
  expect(usage).not.toHaveProperty('quotas');
  expect(usage.observations.length).toBeGreaterThan(0);
});

it('paginates 20 safe rows, preserves hostile text as data and rejects malformed cursors', async () => {
  for (let index = 0; index < 23; index++) await db.prepare(`INSERT INTO contributors
    (contributor_id, discord_user_id, display_name, state, created_at)
    VALUES (?, ?, ?, 'active', ?)`).bind(`page-${String(index).padStart(2, '0')}`, `private-${index}`,
      '<img src=x onerror=alert(1)>', now).run();
  const first = await (await handle('contributors')).json() as Record<string, any>;
  expect(first.items).toHaveLength(20);
  const second = await (await handle(`contributors?after=${encodeURIComponent(first.nextCursor)}`)).json() as Record<string, any>;
  expect(second.items).toHaveLength(4);
  expect(second.nextCursor).toBeNull();
  expect(new Set([...first.items, ...second.items].map(row => row.contributorId)).size).toBe(24);
  expect(second.items[0].displayName).toContain('<img');
  expect((await handle('contributors?after=%%%')).status).toBe(400);
  expect((await handle('operations?after=' + btoa('["wrong-width"]'))).status).toBe(400);
  expect((await handle('contributors?limit=999')).status).toBe(400);
  expect((await handle('contributors/missing/keys')).status).toBe(404);
});

it('ignores legacy activation policy while retaining configuration and query safety', async () => {
  await db.prepare('UPDATE quota_policy SET enabled = 0').run();
  try {
    const value = await (await handle('overview')).json() as Record<string, any>;
    expect(value.activity.resources).toHaveProperty('worker_requests');
    expect(value).not.toHaveProperty('pressure');
  } finally { await db.prepare('UPDATE quota_policy SET enabled = 1').run(); }
  expect((await operatorService(request('overview'), { ...settings, MODERATION_LIMITS: '' }, resolver, new Date(now * 1000))).status).toBe(503);
  expect((await operatorService(request('overview'), { ...settings, MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192,
    maxQueries: 1}) }, resolver, new Date(now * 1000))).status).toBe(413);
  expect(await db.prepare('SELECT count(*) AS total FROM moderation_actions').first()).toEqual({ total: 0 });
});

it('preserves composite operation identities across bounded pages and never exposes saved results', async () => {
  for (let index = 0; index < 23; index++) await db.prepare(`INSERT INTO operations
    (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
    VALUES ('contributor-1', ?, 'same-operation', 'private-digest', 'completed', ?, ?, ?)`)
    .bind(`fixture-${String(index).padStart(2, '0')}`, '{"secret":"private-result"}', now * 1000, now * 1000).run();
  const first = await (await handle('operations')).json() as Record<string, any>;
  const second = await (await handle(`operations?after=${encodeURIComponent(first.nextCursor)}`)).json() as Record<string, any>;
  expect(first.items).toHaveLength(20);
  expect(second.items).toHaveLength(4);
  expect(new Set([...first.items, ...second.items].map(row => `${row.contributorId}:${row.kind}:${row.operationId}`)).size).toBe(24);
  expect(JSON.stringify(first)).not.toContain('private-result');
});

it('bounds historical key pages and normalizes publication timestamps without loading key bytes', async () => {
  for (let index = 0; index < 21; index++) await db.prepare(`INSERT INTO installation_keys
    (key_id, installation_id, algorithm, public_key, state, created_at, retired_at)
    VALUES (?, 'installation-1', 'Ed25519', ?, 'retired', ?, ?)`)
    .bind(`fixture-key-${String(index).padStart(2, '0')}`, new Uint8Array(32), now * 1000, now * 1000).run();
  const first = await (await handle('contributors/contributor-1/keys')).json() as Record<string, any>;
  const second = await (await handle(`contributors/contributor-1/keys?after=${encodeURIComponent(first.nextCursor)}`)).json() as Record<string, any>;
  expect(first.items).toHaveLength(20);
  expect(second.items).toHaveLength(2);
  expect(first.items.every((row: any) => !('public_key' in row))).toBe(true);
  const revisions = await (await handle('contributions')).json() as Record<string, any>;
  expect(revisions.items[0].createdAt).toBe(now * 1000);
});

it('measures bounded local overview costs, separate from hosted billing or provider totals', async context => {
  const observer = observeDownloadDatabase(db, 64);
  const response = await operatorService(request('overview'), { ...settings, CATALOG_DB: observer.db }, resolver, new Date(now * 1000));
  expect(response.status).toBe(200);
  const bytes = new TextEncoder().encode(await response.text()).length, measurement = observer.snapshot();
  expect(measurement.queries).toBeLessThanOrEqual(20);
  expect(bytes).toBeLessThan(131072);
  Object.assign(context.task.meta, { measurement: { ...measurement, bytes, source: 'local_fixture_not_provider_billing' } });
});