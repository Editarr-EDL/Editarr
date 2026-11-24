import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { contributor, type Contributor } from './helpers/submissions';
import { submitWork } from './helpers/master';
import { db, now, setupVersionCatalog } from './helpers/versions';

const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }) };
let jwt: string, resolver: JWTVerifyGetKey, author: Contributor;

beforeAll(async () => {
  await setupVersionCatalog([]);
  author = await contributor();
  await submitWork(author, '700');
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});
const handle = (path: string, token = jwt) => operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/dashboard/${path}`,
  { headers: { 'Cf-Access-Jwt-Assertion': token } }), settings, resolver, new Date(now * 1000));

it('protects every read and exposes no Discord IDs, keys, payloads or operation bodies', async () => {
  for (const path of ['overview', 'contributors', `contributors/${author.contributorId}/keys`,
    `contributors/${author.contributorId}/usage`, 'operations', 'audit']) {
    expect((await handle(path, '')).status).toBe(401);
    const response = await handle(path);
    expect(response.status).toBe(200);
    const body = await response.text();
    for (const forbidden of ['discord_user_id', 'public_key', 'request_digest', 'artifact_bytes', 'result']) expect(body).not.toContain(`"${forbidden}`);
  }
  expect((await handle('contributions')).status).toBe(404);
});

it('reports stored submission bytes and keeps provider values unavailable', async () => {
  const value = await (await handle('overview')).json() as Record<string, any>;
  expect(value.storage).toMatchObject({ source: 'artifact_storage_usage', providerVerified: false });
  expect(value.storage.globalBytes).toBeGreaterThan(0);
  for (const key of ['databaseSize', 'providerUsage', 'backup']) expect(value[key]).toEqual({ source: 'unavailable', observedAt: null, value: null, providerVerified: false });
  expect(value.observations).toEqual([]);
  const usage = await (await handle(`contributors/${author.contributorId}/usage`)).json() as Record<string, any>;
  expect(usage.storage.bytes).toBeGreaterThan(0);
});

it('lists version, alias and moderation actions in one audit page', async () => {
  await db.prepare(`INSERT INTO catalog_moderation_actions (action_id, actor, resource, target_id, command, expected_revision, reason,
    request_json, outcome, error_code, created_at) VALUES ('a-catalog', 'operator', 'version:v', 'v', 'confirm_version', 0, 'r', '{}',
    'denied', 'resource_not_found', 1)`).run();
  await db.prepare(`INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision, reason,
    request_json, outcome, error_code, created_at) VALUES ('b-account', 'operator', 'contributor:c', 'c', 'suspend', 0, 'r', '{}',
    'denied', 'resource_not_found', 2)`).run();
  const page = await (await handle('audit')).json() as { items: { actionId: string; command: string }[] };
  expect(page.items.map(item => [item.actionId, item.command])).toEqual([['a-catalog', 'confirm_version'], ['b-account', 'suspend']]);
});

it('pages a version\'s filters with counts only and its submissions without payloads', async () => {
  const { version_id: versionId } = (await db.prepare(`SELECT version_id FROM submissions WHERE contributor_id = ?`)
    .bind(author.contributorId).first<{ version_id: string }>())!;
  const filters = await (await handle(`versions/${versionId}/filters`)).json() as { items: Record<string, unknown>[]; nextCursor: string | null };
  expect(filters.items.length).toBeGreaterThan(0);
  expect(Object.keys(filters.items[0])).toEqual(['filterId', 'state', 'quarantined', 'category', 'subcategory', 'severity', 'action',
    'startMs', 'endMs', 'confidence', 'contributorCount', 'attestations', 'upvotes', 'downvotes', 'disputes', 'served']);
  expect(filters.items[0].attestations).toBeTypeOf('number');
  const submissions = await (await handle(`versions/${versionId}/submissions`)).json() as { items: Record<string, unknown>[] };
  expect(submissions.items).toEqual([expect.objectContaining({ contributorId: author.contributorId, quarantined: 0 })]);
  expect(JSON.stringify(submissions)).not.toContain('digest');
  expect((await handle('versions/v_missing/filters')).status).toBe(404);
  expect((await handle(`versions/${versionId}/filters?after=!`)).status).toBe(400);
});
