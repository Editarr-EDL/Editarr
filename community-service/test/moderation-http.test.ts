import { tunnelActor } from '../src/runtime/environment.mjs';
import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, generateKeyPair, exportJWK, SignJWT, type JWTVerifyGetKey } from 'jose';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import { db, now, setupPublication, publicationCase, limits } from './helpers/publication';
import { publishArtifact } from '../src/contributions/publication';
import { operatorService } from '../src/admin/service';
import service from '../src/index';

const env = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64}) };
let jwt: string, resolver: JWTVerifyGetKey, revisionId: string;
beforeAll(async () => {
  await setupPublication();
  for (const migration of [imports, votes, moderation]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  revisionId = (await publishArtifact(db, item.identity, item.input, limits, now)).result.revisionId;
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: env.ACCESS_ISSUER, aud: env.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});
const input = (expectedRevision = 0) => ({ version: 1, actionId: crypto.randomUUID(), command: 'quarantine',
  targetId: revisionId, expectedRevision, reason: 'Reviewed report' });
function request(path = '/admin/v1/moderation', body?: unknown) {
  return new Request(`${env.OPERATOR_ORIGIN}${path}`, { method: body ? 'POST' : 'GET', headers: {
    'Cf-Access-Jwt-Assertion': jwt, Origin: env.OPERATOR_ORIGIN, 'X-Community-Operator': '1',
    'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin',
  }, ...(body ? { body: JSON.stringify(body) } : {}) });
}
const handle = (request: Request, settings = env) => operatorService(request, settings, resolver, new Date(now * 1000));

it('denies contributor credentials and hostile origins before any moderation or audit write', async () => {
  for (const [header, value] of [['Cf-Access-Jwt-Assertion', ''], ['Origin', 'https://hostile.example'],
    ['X-Community-Operator', ''], ['Sec-Fetch-Site', 'same-site']]) {
    const hostile = request(undefined, input());
    hostile.headers.set(header, value); hostile.headers.set('Authorization', 'Signature contributor');
    expect((await handle(hostile)).status).toBe(header === 'Cf-Access-Jwt-Assertion' ? 401 : 403);
  }
  expect(await db.prepare('SELECT count(*) AS total FROM moderation_actions').first()).toEqual({ total: 0 });
});

it('inspects snapshot versions, applies actions, returns conflicts and never masks denial with HTML', async () => {
  const inspection = await handle(request(`/admin/v1/moderation/revision/${revisionId}`));
  const state = await inspection.json() as { revision: number };
  const change = input(state.revision);
  const applied = await handle(request(undefined, change));
  expect(applied.status).toBe(200);
  expect(await applied.json()).toMatchObject({ outcome: 'applied', revision: state.revision + 1 });
  expect((await handle(request(undefined, change))).status).toBe(200);
  const stale = await handle(request(undefined, input(state.revision)));
  expect(stale.status).toBe(409);
  expect(stale.headers.get('content-type')).toContain('application/json');
  expect(stale.headers.get('cache-control')).toBe('private, no-store');
  expect(stale.headers.has('access-control-allow-origin')).toBe(false);
  expect((await handle(request('/admin/v1/reports'))).status).toBe(200);
});

it('fails closed for malformed bodies, limits, pressure, alternate hosts and nonlocal activation', async () => {
  expect((await handle(request(undefined, { ...input(), reason: '\u0000' }))).status).toBe(400);
  const compressed = request(undefined, input()); compressed.headers.set('content-encoding', 'gzip');
  expect((await handle(compressed)).status).toBe(400);
  expect((await handle(request(), { ...env, MODERATION_LIMITS: '' })).status).toBe(503);
  expect((await handle(request(undefined, input()), { ...env,
    MODERATION_LIMITS: JSON.stringify({ requestBytes: 10, maxQueries: 64}) })).status).toBe(413);
  const alternate = new Request(`https://preview.example.test/admin/v1/moderation`, request(undefined, input()));
  expect((await handle(alternate)).status).toBe(403);
  expect((await service.fetch(request(), { ...env, APP_ENV: 'production' })).status).toBe(503);
  await db.prepare('UPDATE quota_policy SET enabled = 0').run();
  try { expect((await handle(request('/admin/v1/reports'))).status).toBe(200); }
  finally { await db.prepare('UPDATE quota_policy SET enabled = 1').run(); }
});

it('bounds operator admission and reports query-budget exhaustion without bypassing auth', async () => {
  expect((await handle(request('/admin/v1/reports'), { ...env, MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64}) })).status).toBe(200);
  expect((await handle(request(`/admin/v1/moderation/revision/${revisionId}`), { ...env, MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 1}) })).status).toBe(413);
});