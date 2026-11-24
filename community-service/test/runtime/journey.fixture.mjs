import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { request as httpRequest } from 'node:http';
import { generateKeyPair, exportJWK, SignJWT } from 'jose';
import service, { validateEnvironment } from '../../src/runtime/service.ts';
import { createHttpServer } from '../../src/runtime/http.mjs';
import { tunnelActor, readConfiguration } from '../../src/runtime/environment.mjs';
import { signingKey, signedPost, signedRead } from '../helpers/key-lifecycle.ts';
import { signedEnrollment } from '../helpers/enrollment-signatures.ts';
import { confirmationHash } from '../../src/identity/confirmation.ts';
import { DEFAULT_MASTER_POLICY } from '../../src/filters/policy.ts';

export const origin = 'https://community.example';
export const timestamp = () => Math.floor(Date.now() / 1000);
export const expectStatus = (response, status = 200) => {
  assert.equal(response.status, status, response.text);
  return response.json;
};

// All application traffic goes through a real loopback socket. Only providers are replaced.
export async function journey(database, run, runtime = {}) {
  runtime = { service, validateEnvironment, createHttpServer, readConfiguration, ...runtime };
  const configurationDirectory = mkdtempSync(join(tmpdir(), 'community-config-'));
  const signer = await signingKey();
  const pair = await generateKeyPair('RS256', { extractable: true });
  const jwk = { ...await exportJWK(pair.publicKey), kid: 'journey', alg: 'RS256' };
  const provider = { subject: '1554889314549243999', mode: 'member', calls: [] };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async input => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
    provider.calls.push(url.pathname);
    if (url.href === 'https://journey.cloudflareaccess.com/cdn-cgi/access/certs') return Response.json({ keys: [jwk] });
    assert.equal(url.hostname, 'discord.com', `Unexpected external request: ${url}`);
    if (provider.mode === 'outage') throw new TypeError('synthetic provider outage');
    if (url.pathname.endsWith('/oauth2/token')) return Response.json({ access_token: 'synthetic-token', token_type: 'Bearer', scope: 'identify guilds.members.read' });
    if (url.pathname.endsWith('/users/@me')) return Response.json({ id: provider.subject });
    if (provider.mode === 'nonmember') return Response.json({ code: 10007 }, { status: 404 });
    const member = url.pathname.match(/\/members\/(\d+)$/)?.[1] ?? provider.subject;
    return Response.json({ user: { id: member } });
  };
  const env = { APP_ENV: 'self-hosted', SELF_HOSTED_ENABLED: true, CATALOG_DB: database,
    PUBLIC_ORIGIN: origin, POLICY_VERSION: 'test-policy',
    DISCORD_CLIENT_ID: '1554924079486607490', DISCORD_GUILD_ID: '1554889314549243986',
    DISCORD_CLIENT_SECRET: 'synthetic-secret', DISCORD_BOT_TOKEN: 'synthetic-bot',
    OPERATOR_ORIGIN: 'https://operator.example.test', ACCESS_ISSUER: 'https://journey.cloudflareaccess.com',
    ACCESS_AUDIENCE: 'journey', OPERATOR_SUBJECTS: '["operator"]',
    VOTE_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }),
    REPORT_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }),
    MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }),
    MASTER_POLICY: JSON.stringify(DEFAULT_MASTER_POLICY),
    DOWNLOAD_RECEIPT_PRIVATE_KEY: Buffer.from(await crypto.subtle.exportKey('pkcs8', signer.privateKey)).toString('base64'),
    DOWNLOAD_RECEIPT_KEYS: JSON.stringify([{ keyId: signer.keyId, publicKey: Buffer.from(signer.publicKey).toString('base64'), status: 'active' }]), TRUSTED_ACTOR_KEY: tunnelActor};
  let server;
  try {
    const secretNames = ['DISCORD_CLIENT_SECRET', 'DISCORD_BOT_TOKEN', 'DOWNLOAD_RECEIPT_PRIVATE_KEY'];
    const strings = Object.entries(env).filter(([name, value]) => typeof value === 'string' && name !== 'APP_ENV');
    const configPath = join(configurationDirectory, 'config.json'), secretsPath = join(configurationDirectory, 'secrets.json');
    writeFileSync(configPath, JSON.stringify({ enabled: true, trustedTunnel: true, requestBytes: 262144,
      variables: Object.fromEntries(strings.filter(([name]) => !secretNames.includes(name))) }), { mode: 0o600 });
    writeFileSync(secretsPath, JSON.stringify(Object.fromEntries(strings.filter(([name]) => secretNames.includes(name)))), { mode: 0o600 });
    Object.assign(env, runtime.readConfiguration(configPath, secretsPath).env);
    if (runtime.assets) env.OPERATOR_ASSETS = runtime.assets;
    await runtime.validateEnvironment(env);
    // Synthetic workflow capacity only; retain and test the production request cap separately.
    server = runtime.createHttpServer(request => runtime.service.fetch(request, env), { enabled: true, requestBytes: 262144, env });
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const local = `http://127.0.0.1:${server.address().port}`;
    let sequence = 0;
    async function send(request, additionalHeaders = []) {
      const url = new URL(request.url), bytes = Buffer.from(await request.arrayBuffer());
      const headers = Object.fromEntries(request.headers);
      headers.host = url.host;
      // Distinct trusted synthetic sources avoid obscuring workflow assertions with the separate IP test.
      if (!Object.hasOwn(headers, 'cf-connecting-ip')) headers['cf-connecting-ip'] = `192.0.2.${1 + sequence++ % 200}`;
      if (bytes.length) headers['content-length'] = bytes.length;
      return new Promise((resolve, reject) => {
        const outgoing = httpRequest(local + url.pathname + url.search, { method: request.method,
          headers: [...Object.entries(headers).flat(), ...additionalHeaders] }, response => {
          const chunks = [];
          response.on('data', chunk => chunks.push(chunk));
          response.on('end', () => {
            const text = Buffer.concat(chunks).toString();
            let json; try { json = JSON.parse(text); } catch {}
            resolve({ status: response.statusCode, headers: response.headers, text, json });
          });
        });
        outgoing.on('error', reject); outgoing.end(bytes);
      });
    }
    const read = async (key, path) => send(await signedRead(key, path, timestamp()));
    const post = async (key, path, body, operation = crypto.randomUUID()) =>
      send(await signedPost(key, path, typeof body === 'string' ? body : JSON.stringify(body), operation, timestamp()));
    async function adminToken() {
      return await new SignJWT({ sub: 'operator', type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'journey' })
        .setIssuer(env.ACCESS_ISSUER).setAudience(env.ACCESS_AUDIENCE).setIssuedAt().setExpirationTime('5m').sign(pair.privateKey);
    }
    async function admin(path, body, override = {}) {
      const jwt = await adminToken();
      return send(new Request(env.OPERATOR_ORIGIN + path, { method: body ? 'POST' : 'GET', headers: {
        'Cf-Access-Jwt-Assertion': jwt, Origin: env.OPERATOR_ORIGIN, 'X-Community-Operator': '1',
        'Content-Type': 'application/json', ...override,
      }, ...(body ? { body: JSON.stringify(body) } : {}) }));
    }
    async function authorize(key, subject, old) {
      provider.subject = subject;
      const installationId = old?.installationId ?? crypto.randomUUID();
      const operation = crypto.randomUUID(), secret = 'c'.repeat(64);
      const params = new URLSearchParams({ installationId, keyId: key.keyId,
        publicKey: Buffer.from(key.publicKey).toString('base64'), confirmationHash: await confirmationHash(secret),
        ...(old ? { operationId: operation, oldKeyId: old.keyId } : {}) });
      const start = await send(new Request(`${origin}/v1/discord/${old ? 'recovery-start' : 'start'}?${params}`));
      expectStatus(start, 303);
      assert.match(start.headers['set-cookie'][0], /HttpOnly/);
      const cookie = start.headers['set-cookie'][0].split(';')[0], confirmationUrl = start.headers.location;
      const stateId = new URL(confirmationUrl).searchParams.get('state');
      expectStatus(await send(new Request(confirmationUrl, { headers: { cookie } })));
      expectStatus(await send(new Request(confirmationUrl, { method: 'POST', headers: { cookie, origin } })), 303);
      expectStatus(await send(new Request(`${origin}/v1/discord/callback?state=${stateId}&code=synthetic`, { headers: { cookie } })));
      return { body: { version: 1, stateId, installationId, confirmationSecret: secret,
        ...(old ? { operationId: operation, oldKeyId: old.keyId } : {}) }, operation: old ? operation : stateId, installationId };
    }
    async function enroll(subject) {
      const key = await signingKey(), flow = await authorize(key, subject);
      const profile = expectStatus(await post(key, '/v1/enrollments/complete', flow.body, flow.operation));
      assert.equal(profile.installationId, flow.installationId);
      assert.equal(expectStatus(await post(key, '/v1/enrollments/complete', flow.body, flow.operation)).contributorId, profile.contributorId);
      assert.ok(!JSON.stringify(profile).includes(subject));
      return { ...key, ...profile, subject };
    }
    await run({ database, env, local, send, read, post, admin, adminToken, authorize, enroll, provider, runtime, configuration: { configPath, secretsPath } });
  } finally {
    globalThis.fetch = originalFetch;
    rmSync(configurationDirectory, { recursive: true, force: true });
    if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  }
}

export async function patch(key, body, operation = crypto.randomUUID(), nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64url'), time = timestamp()) {
  return signedEnrollment({ ...key, stateId: operation }, time * 1000, origin + '/v1/identity',
    { method: 'PATCH', body: JSON.stringify(body), operationId: operation, nonce });
}
