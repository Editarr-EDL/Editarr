import assert from 'node:assert/strict';
import { once } from 'node:events';
import { request as httpRequest } from 'node:http';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import service from '../../src/index.ts';
import { operatorService } from '../../src/admin/service.ts';
import { readPublication } from '../../src/contributions/storage.ts';
import { signedPost, signedRead } from '../helpers/key-lifecycle.ts';
import { fixtureKey, syntheticPublication, uploadBody, signedArtifact } from '../helpers/publication-signatures.ts';
import { limits } from '../helpers/publication.ts';
import { verifiedCase } from '../helpers/catalog.ts';
import { createHttpServer } from '../../src/runtime/http.mjs';
import { tunnelActor } from '../../src/runtime/environment.mjs';

async function send(origin, request, bytes) {
  const url = new URL(request.url);
  return new Promise((resolve, reject) => {
    const headers = Object.fromEntries(request.headers);
    headers.host = url.host;
    if (bytes) headers['content-length'] = String(Buffer.byteLength(bytes));
    const incoming = httpRequest(origin + url.pathname, { method: request.method, headers }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, json: JSON.parse(Buffer.concat(chunks).toString()) }));
    });
    incoming.on('error', reject); incoming.end(bytes);
  });
}

export async function exercise(database) {
  const key = await fixtureKey(), now = Math.floor(Date.now() / 1000);
  await database.batch([
    database.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES ('contributor-1', '1554889314549243986', 'Public Name', 'public name', 1)`),
    database.prepare("INSERT INTO installations VALUES ('installation-1', 'contributor-1', 1)"),
    database.prepare("INSERT INTO installation_keys VALUES ('key-1', 'installation-1', 'Ed25519', ?, 'active', 1, NULL)").bind(key.publicKey),
  ]);
  const env = { APP_ENV: 'self-hosted', SELF_HOSTED_ENABLED: true, CATALOG_DB: database,
    PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'test-policy',
    PUBLICATION_LIMITS: JSON.stringify(limits), DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-test-token', TRUSTED_ACTOR_KEY: tunnelActor};
  const authority = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(
    `editarr-membership-v1:${env.DISCORD_GUILD_ID}:${env.DISCORD_BOT_TOKEN}`))).toString('hex');
  await database.prepare("INSERT INTO discord_membership VALUES ('contributor-1', ?, 'eligible', ?)").bind(authority, now).run();
  const server = createHttpServer(request => service.fetch(request, env), {
    enabled: true, requestBytes: limits.requestBytes, env,
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const origin = `http://127.0.0.1:${server.address().port}`;
  const submit = async (input, operation) => {
    const bytes = uploadBody(input);
    const request = await signedPost(key, '/v1/contributions', bytes, operation, now);
    request.headers.set('CF-Connecting-IP', '192.0.2.1');
    return send(origin, request, bytes);
  };
  try {
    const input = await syntheticPublication(), operation = crypto.randomUUID();
    const first = await submit(input, operation);
    assert.equal(first.status, 200, JSON.stringify(first.json));
    const repeated = await submit(input, operation);
    assert.equal(repeated.status, 200, JSON.stringify(repeated.json));
    assert.deepEqual(repeated.json, first.json);
    const row = await database.prepare('SELECT object_key FROM contribution_revisions WHERE revision_id = ?').bind(input.submissionId).first();
    const payload = await readPublication(database, row.object_key);
    assert.deepEqual(payload.artifact.bytes, input.artifactBytes);
    assert.deepEqual(payload.manifest.bytes, input.manifestBytes);
    assert.deepEqual(payload.envelope.bytes, input.envelopeBytes);
    assert.equal(await database.prepare('SELECT COUNT(*) AS count FROM artifact_payloads').first('count'), 1);
    const read = await signedRead(key, `/v1/publication-operations/${operation}`, now);
    read.headers.set('CF-Connecting-IP', '192.0.2.1');
    assert.deepEqual((await send(origin, read)).json, first.json);
    const bytes = uploadBody(input), tampered = await signedPost(key, '/v1/contributions', bytes, crypto.randomUUID(), now);
    tampered.headers.set('CF-Connecting-IP', '192.0.2.1');
    assert.equal((await send(origin, tampered, bytes + 'x')).status, 401);
    const original = structuredClone((await verifiedCase('manual')).artifact.artifact);
    original.submission_id = crypto.randomUUID(); original.sharing_terms = 'unsupported-permission';
    const refused = await submit(await signedArtifact(original), crypto.randomUUID());
    assert.equal(refused.status, 400);
    assert.equal(refused.json.error.code, 'sharing_permission_required');
    assert.equal(await database.prepare('SELECT COUNT(*) AS count FROM artifact_payloads').first('count'), 1);
    assert.equal((await service.fetch(read, { ...env, SELF_HOSTED_ENABLED: false })).status, 503);
    assert.equal((await service.fetch(read, { ...env, APP_ENV: 'production' })).status, 503);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }

  const settings = { OPERATOR_ORIGIN: 'https://operator.example.test', ACCESS_ISSUER: 'https://test.cloudflareaccess.com',
    ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]' };
  const pair = await generateKeyPair('RS256', { extractable: true });
  const resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  const token = await new SignJWT({ sub: 'operator-subject', type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' })
    .setIssuer(settings.ACCESS_ISSUER).setAudience(settings.ACCESS_AUDIENCE).setIssuedAt(now).setExpirationTime(now + 60).sign(pair.privateKey);
  const request = new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/session`, { headers: { 'Cf-Access-Jwt-Assertion': token } });
  assert.equal((await operatorService(request, settings, resolver)).status, 200);
  assert.equal((await operatorService(new Request(request.url), settings, resolver)).status, 401);
  assert.equal((await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/moderation`, {
    method: 'POST', headers: { 'Cf-Access-Jwt-Assertion': token, Origin: 'https://hostile.test' }, body: '{}',
  }), settings, resolver)).status, 403);
}