import { createHash } from 'node:crypto';
import { mkdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { backupDrain } from '../src/backups/runner';
import { backupFixture } from './helpers/backups';

const fixture = backupFixture();
const origin = 'https://operator.example.test';
const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, ...fixture.env, OPERATOR_ORIGIN: origin,
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }) };
const now = new Date('2026-11-25T10:00:00Z');
const seconds = now.getTime() / 1000;
const csrf = { Origin: origin, 'X-Community-Operator': '1', 'Sec-Fetch-Site': 'same-origin' };
let jwt: string, resolver: JWTVerifyGetKey;

beforeAll(async () => {
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: seconds + 60, iat: seconds - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});
afterAll(() => fixture.close());

function call(path: string, init: { method?: string; body?: unknown; headers?: Record<string, string>; token?: string | null } = {},
  env: Record<string, unknown> = settings) {
  const headers: Record<string, string> = { ...(init.token === null ? {} : { 'Cf-Access-Jwt-Assertion': init.token ?? jwt }),
    ...(init.method && init.method !== 'GET' ? csrf : {}), ...init.headers };
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';
  return operatorService(new Request(`${origin}/admin/v1/backups${path}`, { method: init.method ?? 'GET', headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body) }), env, resolver, now);
}
const config = { enabled: true, frequency: 'daily', atUtc: '03:30', keepLast: 3, keepWeekly: 2, destination: 'nightly', integrityCheck: true };
const audits = () => fixture.database.connection.prepare(
  "SELECT command, target_id, actor FROM moderation_actions WHERE resource = 'backup' ORDER BY created_at, command").all();

describe('operator backup API', () => {
  it('requires operator authentication and CSRF protection on every mutation', async () => {
    expect((await call('', { token: null })).status).toBe(401);
    for (const [method, path] of [['PUT', '/config'], ['POST', '/run'], ['POST', '/test-destination']]) {
      const response = await call(path, { method, body: config, headers: { Origin: 'https://hostile.test' } });
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual({ error: { code: 'operator_csrf_denied' } });
      const missing = await operatorService(new Request(`${origin}/admin/v1/backups${path}`, { method,
        headers: { 'Cf-Access-Jwt-Assertion': jwt, Origin: origin } }), settings, resolver, now);
      expect(missing.status).toBe(403);
    }
    expect(audits()).toEqual([]);
  });

  it('reports the default configuration, root and no schedule while disabled', async () => {
    const response = await call('');
    expect(response.status).toBe(200);
    expect(response.headers.get('Cache-Control')).toBe('private, no-store');
    expect(await response.json()).toMatchObject({ version: 1, observedAt: now.getTime(), running: false, runs: [], next: null,
      config: { enabled: false, frequency: 'daily', atUtc: '03:30', keepLast: 14, keepWeekly: 8, destination: '', integrityCheck: true },
      destination: { configured: true, writable: true, root: fixture.root, path: fixture.root, error: null } });
    expect((await call('?x=1')).status).toBe(400);
  });

  it('validates, confines and audits configuration changes', async () => {
    expect((await call('/config', { method: 'PUT', body: { ...config, extra: 1 } })).status).toBe(400);
    expect((await call('/config', { method: 'PUT', body: { ...config, atUtc: '24:00' } })).status).toBe(400);
    for (const [destination, code] of [['/etc', 'backup_destination_outside_root'], ['../x', 'backup_destination_outside_root'],
      ['staged', 'backup_destination_invalid']]) {
      const response = await call('/config', { method: 'PUT', body: { ...config, destination } });
      expect([response.status, await response.json()]).toEqual([400, { error: { code } }]);
    }
    mkdirSync(join(fixture.directory, 'elsewhere'));
    symlinkSync(join(fixture.directory, 'elsewhere'), join(fixture.root, 'link'));
    expect(await (await call('/config', { method: 'PUT', body: { ...config, destination: 'link' } })).json())
      .toEqual({ error: { code: 'backup_destination_symlink' } });
    const unconfigured = await call('/config', { method: 'PUT', body: config }, { ...settings, COMMUNITY_BACKUP_ROOT: undefined });
    expect([unconfigured.status, await unconfigured.json()]).toEqual([503, { error: { code: 'backups_not_configured' } }]);
    expect(audits()).toEqual([]);
    const saved = await call('/config', { method: 'PUT', body: { ...config, destination: `${fixture.root}/nightly` } });
    expect(saved.status).toBe(200);
    expect(await saved.json()).toMatchObject({ config: { ...config, updatedAt: now.getTime(), updatedBy: 'operator-subject' },
      destination: { writable: true, path: join(fixture.root, 'nightly') }, next: { at: Date.parse('2026-11-26T03:30:00Z') } });
    expect(audits()).toEqual([{ command: 'backup_configure', target_id: 'config', actor: 'operator-subject' }]);
  });

  it('tests a destination without saving it', async () => {
    const body = await (await call('/test-destination', { method: 'POST', body: { destination: 'probe-only' } })).json();
    expect(body.destination).toMatchObject({ writable: true, path: join(fixture.root, 'probe-only'), error: null });
    expect(body.destination.freeBytes).toBeGreaterThan(0);
    expect((await call('/test-destination', { method: 'POST', body: { destination: '/tmp' } })).status).toBe(400);
    const unconfigured = await call('/test-destination', { method: 'POST' }, { ...settings, COMMUNITY_BACKUP_ROOT: undefined });
    expect(unconfigured.status).toBe(503);
  });

  it('runs one backup at a time, then downloads and stages the verified file', async () => {
    const started = await call('/run', { method: 'POST' });
    expect(started.status).toBe(202);
    const { runId } = await started.json();
    expect((await call('/run', { method: 'POST' })).status).toBe(409);
    expect((await call('')).headers.get('Content-Type')).toContain('json');
    expect((await (await call('')).json()).running).toBe(true);
    await backupDrain();
    const view = await (await call('')).json();
    expect(view.running).toBe(false);
    expect(view.runs[0]).toMatchObject({ runId, status: 'verified', trigger: 'manual', actor: 'operator-subject', integrity: 'ok' });
    expect(view.runs[0].fileName).toMatch(/^nightly\/community-\d{8}T\d{6}Z-[0-9a-f]{8}\.sqlite$/);

    const download = await call(`/${runId}/download`);
    expect(download.status).toBe(200);
    expect(download.headers.get('Content-Type')).toBe('application/vnd.sqlite3');
    expect(download.headers.get('Content-Disposition')).toBe(`attachment; filename="${view.runs[0].fileName.split('/')[1]}"`);
    expect(download.headers.get('Cache-Control')).toBe('private, no-store');
    expect(download.headers.get('X-Content-Type-Options')).toBe('nosniff');
    const bytes = Buffer.from(await download.arrayBuffer());
    expect(bytes.length).toBe(view.runs[0].bytes);
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(view.runs[0].sha256);
    expect((await call(`/${runId}/download`, { method: 'HEAD' })).status).toBe(405);
    expect((await call('/00000000-0000-4000-8000-000000000000/download')).status).toBe(404);

    expect((await call(`/${runId}/stage`, { method: 'POST', body: { unexpected: true } })).status).toBe(400);
    const staged = await call(`/${runId}/stage`, { method: 'POST' });
    expect(staged.status).toBe(200);
    const body = await staged.json();
    expect(body).toMatchObject({ runId, sha256: view.runs[0].sha256, stagedPath: join(fixture.root, 'staged', view.runs[0].fileName.split('/')[1]) });
    expect(body.commands.at(-1)).toContain(`restore --confirm --source ${body.stagedPath} --sha256 ${body.sha256}`);
    expect(audits().map(row => row.command)).toEqual(['backup_configure', 'backup_run', 'backup_stage']);
  });

  it('refuses download and staging for failed runs', async () => {
    fixture.database.connection.exec(`INSERT INTO backup_runs (run_id, started_at, trigger, status, error_code)
      VALUES ('11111111-1111-4111-8111-111111111111', 1, 'manual', 'failed', 'snapshot_failed')`);
    for (const response of [await call('/11111111-1111-4111-8111-111111111111/download'),
      await call('/11111111-1111-4111-8111-111111111111/stage', { method: 'POST' })])
      expect([response.status, await response.json()]).toEqual([409, { error: { code: 'backup_not_verified' } }]);
  });

  it('refuses a manual run without a backup root', async () => {
    const response = await call('/run', { method: 'POST' }, { ...settings, COMMUNITY_BACKUP_ROOT: undefined });
    expect([response.status, await response.json()]).toEqual([503, { error: { code: 'backups_not_configured' } }]);
    const view = await (await call('', {}, { ...settings, COMMUNITY_BACKUP_ROOT: undefined })).json();
    expect(view).toMatchObject({ next: null, destination: { configured: false, root: null, error: 'backups_not_configured' } });
  });
});
