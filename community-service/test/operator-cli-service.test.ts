import { tunnelActor } from '../src/runtime/environment.mjs';
import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, generateKeyPair, exportJWK, SignJWT, type JWTVerifyGetKey } from 'jose';
import download from '../migrations/0011_download_usage.sql?raw';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import { db, now, setupPublication, publicationCase, limits } from './helpers/publication';
import { publishArtifact } from '../src/contributions/publication';
import { operatorService } from '../src/admin/service';
import { operatorClient } from '../scripts/operator/transport.mjs';
import { inspectModeration } from '../src/admin/moderation';
import contract from '../../tests/fixtures/community/moderation-v1.json';

const env = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64}) };
let jwt: string, resolver: JWTVerifyGetKey, revisionId: string;
beforeAll(async () => {
  await setupPublication();
  for (const migration of [download, imports, votes]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  revisionId = (await publishArtifact(db, item.identity, item.input, limits, now)).result.revisionId;
  await db.exec(moderation.replace(/\s*\n\s*/g, ' '));
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: env.ACCESS_ISSUER, aud: env.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});

function client(settings = env, forwarded = true) {
  return operatorClient(env.OPERATOR_ORIGIN, jwt, async (url, options) => {
    const request = new Request(url, { method: options.method, headers: options.headers,
      ...(options.body ? { body: options.body } : {}) });
    expect(request.headers.get('Cf-Access-Token')).toBe(jwt);
    expect(request.headers.has('Sec-Fetch-Site')).toBe(false);
    if (forwarded) request.headers.set('Cf-Access-Jwt-Assertion', jwt);
    request.headers.delete('Cf-Access-Token');
    return operatorService(request, settings, resolver, new Date(now * 1000));
  });
}

it('CLI credentials alone cannot bypass the Access edge or human subject allowlist', async () => {
  await expect(client(env, false)('/admin/v1/session')).rejects.toMatchObject({ status: 401 });
  await expect(client({ ...env, OPERATOR_SUBJECTS: '["other-human"]' })('/admin/v1/session'))
    .rejects.toMatchObject({ status: 403, code: 'operator_not_allowed' });
  expect(await client()('/admin/v1/session')).toEqual({ version: 1, subject: 'operator-subject' });
  expect(await db.prepare('SELECT count(*) AS total FROM moderation_actions').first()).toEqual({ total: 0 });
});

it('CLI and dashboard share the exact fixture result, actor-bound retries, optimistic conflicts and immutable audit', async () => {
  expect(revisionId).toBe(contract.action.targetId);
  const request = client();
  expect(await request('/admin/v1/moderation', contract.action)).toEqual(contract.applied);
  expect(await request('/admin/v1/moderation', contract.action)).toEqual(contract.applied);
  const inspected = await request(`/admin/v1/moderation/revision/${revisionId}`);
  expect(inspected).toEqual(await inspectModeration(db, 'revision', revisionId));
  expect(inspected).toMatchObject({ audit: [{ command: 'quarantine', outcome: 'applied' }] });
  expect(await db.prepare('SELECT actor FROM moderation_actions WHERE action_id = ?')
    .bind(contract.action.actionId).first()).toEqual({ actor: 'operator-subject' });
  await expect(request('/admin/v1/moderation', { ...contract.action, actionId: crypto.randomUUID() }))
    .rejects.toMatchObject({ status: 409, code: 'moderation_conflict' });
  const restored = { ...contract.action, actionId: crypto.randomUUID(), command: 'restore', expectedRevision: 1 };
  expect(await request('/admin/v1/moderation', restored)).toMatchObject({ revision: 2, outcome: 'applied' });
  expect(await request('/admin/v1/moderation', contract.action)).toEqual(contract.applied);
  expect(await request(`/admin/v1/moderation/revision/${revisionId}`)).toMatchObject({ revision: 2, state: { state: 'published' } });
});

it('CLI reads the same dashboard DTOs and never claims unavailable provider or backup measurements', async () => {
  expect(await client()('/admin/v1/dashboard/overview')).toMatchObject({ databaseSize: { source: 'unavailable', value: null },
    providerUsage: { source: 'unavailable', value: null }, backup: { source: 'unavailable', value: null } });
  expect(await client()('/admin/v1/dashboard/operations')).toMatchObject({ version: 1, source: 'service_database', nextCursor: null });
});