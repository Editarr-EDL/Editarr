import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import service from '../src/index';
import { recordUsage } from '../src/budgets/usage';
import { contributor, type Contributor } from './helpers/submissions';
import { submitWork } from './helpers/master';
import { db, now, setupVersionCatalog } from './helpers/versions';

const HOUR = 3_600_000, DAY = 86_400_000;
const live = Math.floor(Date.now() / 1000);
const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }), DATABASE_WAL_BYTES: () => 4096 };
let jwt: string, resolver: JWTVerifyGetKey, author: Contributor, other: Contributor;

beforeAll(async () => {
  await setupVersionCatalog([]);
  [author, other] = [await contributor(), await contributor()];
  await submitWork(author, '900');
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  // Valid both at the fixture clock (`now`) and the live clock the maintenance job uses.
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: live + 600, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});

type Body = Record<string, any>;
const get = async (path: string, at = live, token = jwt) => {
  const response = await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/${path}`,
    { headers: { 'Cf-Access-Jwt-Assertion': token } }), settings, resolver, new Date(at * 1000));
  return { status: response.status, headers: response.headers, body: await response.json() as Body };
};

it('protects every system read and refuses unknown, repeated or invalid query parameters', async () => {
  for (const path of ['health', 'trends?metric=db', 'maintenance/runs?kind=retention']) {
    expect((await get(path, live, '')).status).toBe(401);
    const ok = await get(path);
    expect(ok.status).toBe(200);
    expect(ok.headers.get('Cache-Control')).toBe('private, no-store');
    expect(ok.body).toMatchObject({ version: 1, observedAt: live * 1000 });
  }
  for (const [path, code] of [['health?x=1', 'invalid_health_query'], ['trends', 'invalid_trends_query'],
    ['trends?metric=cpu', 'invalid_trends_query'], ['trends?metric=db&period=1y', 'invalid_trends_query'],
    ['trends?metric=db&metric=db', 'invalid_trends_query'], ['trends?metric=db&extra=1', 'invalid_trends_query'],
    ['maintenance/runs', 'invalid_maintenance_query'], ['maintenance/runs?kind=metrics', 'invalid_maintenance_query'],
    ['maintenance/runs?kind=retention&after=!', 'invalid_maintenance_query'],
    ['maintenance/runs?kind=retention&after=' + btoa('["x"]'), 'invalid_maintenance_query']])
    expect(await get(path)).toMatchObject({ status: 400, body: { error: { code } } });
  const post = await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/health`, { method: 'POST',
    headers: { 'Cf-Access-Jwt-Assertion': jwt } }), settings, resolver, new Date(live * 1000));
  expect(post.status).toBeGreaterThanOrEqual(400);
});

it('records metrics, usage rollups and a retention run before pruning, and reports them in health', async () => {
  const before = await get('health');
  expect(before.body).toMatchObject({ ready: false, lastRetention: null, lastRecomputeAll: null,
    database: { walBytes: 4096 }, storage: { quotaBytes: 350_000_000 } });
  expect(before.body.database.bytes).toBeGreaterThan(0);
  expect(before.body.storage.payloadBytes).toBeGreaterThan(0);
  expect(before.body.checks.find((check: Body) => check.id === 'schema')).toMatchObject({ ok: true });
  expect(before.body.checks.find((check: Body) => check.id === 'backups')).toMatchObject({ ok: false, detail: 'Scheduled backups are off' });
  for (const person of [author, other]) for (const outcome of ['allowed', 'denied'] as const)
    await recordUsage(db, { contributorId: person.contributorId, resource: 'worker_requests', outcome, units: 3, now: Date.now() });
  await db.prepare(`INSERT INTO service_metrics_hourly (hour, db_bytes, source) VALUES (0, 1, 'service_database')`).run();
  await service.scheduled({}, { APP_ENV: 'local', CATALOG_DB: db, DATABASE_WAL_BYTES: () => 8192 });
  const day = new Date().toISOString().slice(0, 10);
  expect((await db.prepare(`SELECT outcome, occurrences, units, contributors FROM usage_daily WHERE day = ? ORDER BY outcome`)
    .bind(day).all()).results).toEqual([{ outcome: 'allowed', occurrences: 2, units: 6, contributors: 2 },
    { outcome: 'denied', occurrences: 2, units: 6, contributors: 2 }]);
  const samples = (await db.prepare('SELECT hour, wal_bytes AS wal FROM service_metrics_hourly').all<{ hour: number; wal: number }>()).results;
  expect(samples).toEqual([{ hour: Math.floor(Date.now() / HOUR) * HOUR, wal: 8192 }]);
  const after = await get('health');
  expect(after.body.lastRetention).toMatchObject({ kind: 'retention', outcome: 'ok', detail: { failedSteps: [] } });
  expect(after.body.checks.find((check: Body) => check.id === 'retention')).toMatchObject({ ok: true });
  const trend = await get('trends?metric=denials&period=7d');
  expect(trend.body).toMatchObject({ metric: 'denials', period: '7d', unit: 'requests', total: 2, previousTotal: 0,
    provenance: 'application_observed', bucketMs: DAY });
  expect(trend.body.points).toHaveLength(7);
  expect(trend.body.points.at(-1)).toEqual({ t: Date.parse(`${day}T00:00:00Z`), v: 2 });
  const requests = await get('trends?metric=requests&period=24h');
  expect(requests.body).toMatchObject({ total: 12, total2: 2, points: [{ v: 12, v2: 2 }] });
});

it('buckets gauges and counters, compares with the previous period and fills empty counter buckets', async () => {
  const at = now + 3600, end = Math.floor(at * 1000 / HOUR) * HOUR + HOUR;
  for (const [hour, bytes] of [[end - 2 * HOUR, 100], [end - HOUR, 300], [end - DAY - HOUR, 50]])
    await db.prepare(`INSERT INTO service_metrics_hourly (hour, db_bytes, wal_bytes, payload_bytes, payload_quota_bytes, source)
      VALUES (?, ?, 7, 5, 9, 'service_database')`).bind(hour, bytes).run();
  const gauge = await get('trends?metric=db&period=24h', at);
  expect(gauge.body).toMatchObject({ unit: 'bytes', bucketMs: HOUR, total: 300, previousTotal: 50, total2: 7,
    provenance: 'service_database', points: [{ t: end - 2 * HOUR, v: 100, v2: 7 }, { t: end - HOUR, v: 300, v2: 7 }] });
  const storage = await get('trends?metric=storage&period=30d', at);
  expect(storage.body).toMatchObject({ bucketMs: DAY, total: 5, total2: 9 });
  const uploads = await get('trends?metric=uploads_votes&period=24h', at);
  expect(uploads.body.points).toHaveLength(24);
  expect(uploads.body).toMatchObject({ unit: 'events', total: 1, previousTotal: 0, total2: 0 });
  expect(uploads.body.points.find((point: Body) => point.v === 1)).toEqual({ t: Math.floor(now * 1000 / HOUR) * HOUR, v: 1, v2: 0 });
  const downloads = await get('trends?metric=downloads&period=90d', at);
  expect(downloads.body).toMatchObject({ unit: 'downloads', bucketMs: DAY, total2: null });
  expect(downloads.body.points).toHaveLength(90);
});

it('pages maintenance runs newest first by kind with an opaque cursor', async () => {
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO maintenance_runs (run_id, kind, started_at, duration_ms, outcome, detail_json)
    SELECT printf('r%02d', i), 'recompute_all', 1000 + i, 5, CASE WHEN i = 22 THEN 'batch_limit' ELSE 'ok' END,
      CASE WHEN i = 1 THEN 'not json' ELSE '{"versions":200}' END FROM n`).run();
  const first = await get('maintenance/runs?kind=recompute_all');
  expect(first.body.items).toHaveLength(20);
  expect(first.body.items[0]).toEqual({ runId: 'r22', kind: 'recompute_all', startedAt: 1022, durationMs: 5, outcome: 'batch_limit',
    detail: { versions: 200 } });
  const rest = await get(`maintenance/runs?kind=recompute_all&after=${encodeURIComponent(first.body.nextCursor)}`);
  expect(rest.body).toMatchObject({ nextCursor: null, items: [{ runId: 'r02' }, { runId: 'r01', detail: {} }] });
  expect((await get('health')).body.lastRecomputeAll).toMatchObject({ runId: 'r22' });
});
