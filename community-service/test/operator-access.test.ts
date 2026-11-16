import { tunnelActor } from '../src/runtime/environment.mjs';
import type { AssetFetcher } from '../src/runtime/contracts';
import { env as runtimeEnv } from './helpers/environment';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWK, type JWTVerifyGetKey } from 'jose';
import { authenticateOperator, operatorConfig, requireOperatorMutation } from '../src/admin/auth/access';
import { operatorService } from '../src/admin/service';
import service from '../src/index';

const env = { OPERATOR_ORIGIN: 'https://operator.example.test', ACCESS_ISSUER: 'https://test.cloudflareaccess.com',
  ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]' };
const config = operatorConfig(env);
const now = new Date('2026-10-04T12:00:00Z');
const timestamp = now.getTime() / 1000;
let privateKey: CryptoKey;
let resolver: JWTVerifyGetKey;
let publicKey: JWK;
beforeAll(async () => {
  const pair = await generateKeyPair('RS256', { extractable: true });
  privateKey = pair.privateKey;
  publicKey = { ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' };
  resolver = createLocalJWKSet({ keys: [publicKey] });
});
async function token(claims: Record<string, unknown> = {}, key = privateKey): Promise<string> {
  return new SignJWT({ sub: 'operator-subject', iss: config.issuer, aud: config.audience,
    exp: timestamp + 60, iat: timestamp - 60, type: 'app', ...claims })
    .setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(key);
}
function request(jwt?: string, origin = config.origin): Request {
  return new Request(`${origin}/admin/v1/session`, { headers: jwt ? { 'Cf-Access-Jwt-Assertion': jwt } : {} });
}

describe('operator Access boundary', () => {
  it('accepts a verified allowlisted application subject', async () => {
    expect(await authenticateOperator(request(await token()), config, resolver, now)).toEqual({ subject: 'operator-subject' });
  });
  it.each([
    { aud: 'contributor-app' }, { iss: 'https://foreign.cloudflareaccess.com' }, { exp: timestamp },
    { nbf: timestamp + 1 }, { iat: timestamp + 1 }, { sub: 'contributor-subject' }, { type: 'service' },
    { exp: undefined }, { sub: undefined },
  ])('rejects invalid claims %j', async (claims) => {
    await expect(authenticateOperator(request(await token(claims)), config, resolver, now)).rejects.toThrow();
  });
  it('rejects forged identity headers, contributor authorization and missing JWT', async () => {
    const forged = request();
    forged.headers.set('Cf-Access-Authenticated-User-Email', 'operator@example.test');
    forged.headers.set('Authorization', 'Signature contributor');
    await expect(authenticateOperator(forged, config, resolver, now)).rejects.toMatchObject({ status: 401 });
  });
  it('rejects a forged signature and duplicate assertion', async () => {
    const other = await generateKeyPair('RS256');
    await expect(authenticateOperator(request(await token({}, other.privateKey)), config, resolver, now)).rejects.toThrow();
    const jwt = await token();
    await expect(authenticateOperator(request(`${jwt}, ${jwt}`), config, resolver, now)).rejects.toThrow();
  });
  it('rejects algorithm substitution and oversized/malformed tokens', async () => {
    const jwt = await new SignJWT({ sub: 'operator-subject', type: 'app' })
      .setProtectedHeader({ alg: 'HS256', kid: 'fixture' }).sign(new Uint8Array(32));
    for (const invalid of [jwt, 'a'.repeat(16385), 'not-a-jwt'])
      await expect(authenticateOperator(request(invalid), config, resolver, now)).rejects.toThrow();
  });
  it('rejects alternate origins even with valid tokens', async () => {
    for (const origin of ['https://preview.example.test', 'https://service.account.workers.dev', 'http://operator.example.test'])
      await expect(authenticateOperator(request(await token(), origin), config, resolver, now)).rejects.toMatchObject({ status: 403 });
  });
  it('fails closed on key provider failure', async () => {
    await expect(authenticateOperator(request(await token()), config, async () => { throw new TypeError('offline'); }, now))
      .rejects.toMatchObject({ status: 503, code: 'operator_verification_unavailable' });
  });
  it.each([
    {}, { ...env, OPERATOR_SUBJECTS: '[]' }, { ...env, ACCESS_ISSUER: 'https://attacker.test' },
    { ...env, OPERATOR_ORIGIN: 'https://service.account.workers.dev' }, { ...env, OPERATOR_ORIGIN: `${env.OPERATOR_ORIGIN}/` },
  ])('fails closed on unconfigured operator settings %j', (settings) => {
    expect(() => operatorConfig(settings)).toThrow('operator_not_configured');
  });
  it('requires exact same origin, custom header and same-origin browser context on mutations', () => {
    const mutation = new Request(`${config.origin}/admin/v1/action`, { method: 'POST',
      headers: { Origin: config.origin, 'X-Community-Operator': '1', 'Sec-Fetch-Site': 'same-origin' } });
    expect(() => requireOperatorMutation(mutation, config)).not.toThrow();
    for (const [name, value] of [['Origin', 'https://hostile.test'], ['Origin', 'null'],
      ['X-Community-Operator', ''], ['Sec-Fetch-Site', 'same-site']]) {
      const rejected = new Request(mutation);
      rejected.headers.set(name, value);
      expect(() => requireOperatorMutation(rejected, config)).toThrow('operator_csrf_denied');
    }
  });
});

describe('operator route matrix', () => {
  const paths = ['/admin', '/admin/', '/admin/index.html', '/admin/assets/index.js',
    '/admin/v1', '/admin/v1/session', '/admin/v1/unknown', '/admin/unknown',
    '/admin/v1/dashboard/overview', '/admin/v1/dashboard/contributions', '/admin/v1/dashboard/contributors',
    '/admin/v1/dashboard/operations', '/admin/v1/dashboard/audit'];
  it.each(paths)('denies %s before any asset or contributor access', async (path) => {
    const response = await service.fetch(new Request(`${config.origin}${path}`, { headers: {
      'Cf-Access-Authenticated-User-Email': 'operator@example.test', Authorization: 'Signature contributor',
    } }), { ...env, APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, OPERATOR_ASSETS: { fetch: async () => { throw new Error('must not serve'); } } });
    expect(response.status).toBe(401);
    expect(response.headers.get('Cache-Control')).toContain('no-store');
    expect(response.headers.has('Access-Control-Allow-Origin')).toBe(false);
    expect(await response.json()).toEqual({ error: { code: 'operator_authentication_required' } });
  });
  it('serves only protected allowlisted assets and never masks API errors', async () => {
    const seen: Request[] = [];
    const assets = { fetch: async (input: Request) => {
      seen.push(input);
      return new Response('<html>fixture</html>', { headers: { 'Content-Type': 'text/html', 'Set-Cookie': 'unsafe=1' } });
    } } as AssetFetcher;
    const jwt = await token();
    for (const path of paths) {
      const response = await operatorService(new Request(`${config.origin}${path}`, {
        headers: { 'Cf-Access-Jwt-Assertion': jwt, Cookie: 'CF_Authorization=private' },
      }), { ...env, OPERATOR_ASSETS: assets }, resolver, now);
      const isAsset = ['/admin', '/admin/', '/admin/index.html', '/admin/assets/index.js'].includes(path);
      expect(response.status).toBe(isAsset || path === '/admin/v1/session' ? 200 : path.startsWith('/admin/v1/dashboard/') ? 503 : 404);
      expect(response.headers.get('Cache-Control')).toContain('no-store');
      expect(response.headers.get('Content-Security-Policy')).toContain("frame-ancestors 'none'");
      expect(response.headers.has('Set-Cookie')).toBe(false);
      if (path === '/admin/v1/session') expect(await response.json()).toEqual({ version: 1, subject: 'operator-subject' });
      if (path.startsWith('/admin/v1')) expect(response.headers.get('Content-Type')).toContain('application/json');
    }
    expect(seen.map((input) => new URL(input.url).pathname)).toEqual(['/index.html', '/index.html', '/index.html', '/assets/index.js']);
    expect(seen.every((input) => !input.headers.has('Cookie') && !input.headers.has('Cf-Access-Jwt-Assertion'))).toBe(true);
  });
  it('denies direct assets, alternate hosts, unconfigured settings and nonlocal activation', async () => {
    const jwt = await token();
    for (const path of ['/index.html', '/assets/index.js', '/unknown', '/v1/admin']) {
      expect((await service.fetch(new Request(`${config.origin}${path}`), { ...env, APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor })).status).toBe(404);
    }
    for (const origin of ['https://service.account.workers.dev', 'https://preview.example.test']) {
      const response = await operatorService(new Request(`${origin}/admin/`, { headers: { 'Cf-Access-Jwt-Assertion': jwt } }), env, resolver, now);
      expect(response.status).toBe(403);
    }
    expect((await operatorService(request(jwt), {}, resolver, now)).status).toBe(503);
    expect((await service.fetch(request(jwt), { ...env, APP_ENV: 'production' })).status).toBe(503);
  });
  it('denies cross-origin mutations and preflight without creating an API fallback', async () => {
    const jwt = await token();
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
      const input = new Request(`${config.origin}/admin/v1/unknown`, { method, headers: { 'Cf-Access-Jwt-Assertion': jwt } });
      expect((await operatorService(input, env, resolver, now)).status).toBe(403);
      input.headers.set('Origin', config.origin);
      input.headers.set('X-Community-Operator', '1');
      expect((await operatorService(input, env, resolver, now)).status).toBe(404);
    }
  });
  it('serves no direct assets, unknown paths or SPA fallback through the container asset adapter', async () => {
    for (const path of ['/admin/', '/unknown', '/v1/unknown', '/assets/nested/file.js']) {
      const response = await runtimeEnv.OPERATOR_ASSETS.fetch(new Request(`${config.origin}${path}`));
      expect(response.status).toBe(404);
    }
  });
  it('verifies remote Access keys through real dispatch and serves the isolated built asset binding', async () => {
    const current = Math.floor(Date.now() / 1000);
    const jwt = await token({ exp: current + 60, iat: current - 60 });
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      expect(String(input)).toBe(`${config.issuer}/cdn-cgi/access/certs`);
      return Response.json({ keys: [publicKey] });
    });
    try {
      const configured = { ...env, APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, OPERATOR_ASSETS: runtimeEnv.OPERATOR_ASSETS };
      const headers = { 'Cf-Access-Jwt-Assertion': jwt };
      const response = await service.fetch(new Request(`${config.origin}/admin/`, { headers }), configured);
      expect(response.status).toBe(200);
      const html = await response.text();
      expect(html).toContain('Editarr Community Operators');
      expect(html).not.toContain('editarr-bootstrap');
      const bundle = html.match(/src="(\/admin\/assets\/[^\"]+\.js)"/)?.[1];
      expect(bundle).toBeDefined();
      const script = await service.fetch(new Request(`${config.origin}${bundle}`, { headers }), configured);
      expect(script.status).toBe(200);
      const code = await script.text();
      expect(code).toContain('/admin/v1/session');
      expect(code).not.toContain('/api/auth');
      expect(code).not.toContain('editarr-bootstrap');
      expect(code).not.toContain('DISCORD_CLIENT_SECRET');
      const own = await service.fetch(request(jwt), configured);
      expect(await own.json()).toEqual({ version: 1, subject: 'operator-subject' });
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally { fetcher.mockRestore(); }
  });
});