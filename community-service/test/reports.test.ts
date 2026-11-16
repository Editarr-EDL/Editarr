import { tunnelActor } from '../src/runtime/environment.mjs';
import type { SqlStatement } from '../src/runtime/contracts';
import { beforeAll, expect, it, vi } from 'vitest';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import localReports from '../migrations/0016_local_reports.sql?raw';
import membership from '../migrations/0007_membership.sql?raw';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { contributorReport } from '../src/http/reports';
import { signedPost, signingKey, type SigningKey } from './helpers/key-lifecycle';
import { fixtureKey } from './helpers/publication-signatures';
import { inspectModeration, moderate } from '../src/admin/moderation';
import { observeDownloadDatabase } from '../src/budgets/download-usage';
import service from '../src/index';
import contract from '../../tests/fixtures/community/moderation-v1.json';

let revisionId: string, key: SigningKey, other: SigningKey;
const caps = { requestBytes: 8192, maxQueries: 64};
const path = () => `/v1/contributions/${revisionId}/reports`;
const request = (body: unknown = contract.report, operationId = crypto.randomUUID(), signer = key, timestamp = now) =>
  signedPost(signer, path(), JSON.stringify(body), operationId, timestamp);
const handle = (request: Request, membership = async () => 'eligible' as const, limits = caps) =>
  contributorReport(request, db, 'community.example', now, membership, limits);
beforeAll(async () => {
  await setupPublication();
  for (const migration of [imports, votes, moderation, localReports, membership]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  const result = (await publishArtifact(db, item.identity, item.input, limits, now)).result;
  revisionId = result.revisionId;
  key = await signingKey(); other = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('reporter', '1554889314549243999', 'Reporter', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('reporter-install', 'reporter', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'reporter-install', 'Ed25519', ?, 'active', 1, NULL)").bind(key.keyId, key.publicKey),
    db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)').bind('reporter', revisionId, 'a'.repeat(64), result.artifactDigest, now),
    db.prepare("INSERT INTO contributors VALUES ('other', 'discord-other', 'Other', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('other-install', 'other', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'other-install', 'Ed25519', ?, 'active', 1, NULL)").bind(other.keyId, other.publicKey),
  ]);
});

async function resolveOpen() {
  const row = await db.prepare("SELECT report_id FROM reports WHERE state = 'open' AND contributor_id = 'reporter'").first<{ report_id: string }>();
  if (!row) return;
  const inspected = await inspectModeration(db, 'report', row.report_id);
  await moderate(db, 'operator', { version: 1, actionId: crypto.randomUUID(), command: 'resolve_report',
    targetId: row.report_id, expectedRevision: inspected.revision, reason: 'Reviewed without automatic content action' }, now);
}

it('commits one private report across concurrent retries without votes or automatic quarantine', async () => {
  const operationId = crypto.randomUUID();
  const outputs = await Promise.all(Array.from({ length: 4 }, async () => handle(await request(contract.report, operationId))));
  expect(outputs.map(output => output.status)).toEqual([200, 200, 200, 200]);
  const results = await Promise.all(outputs.map(output => output.json()));
  expect(results.every(result => JSON.stringify(result) === JSON.stringify(results[0]))).toBe(true);
  expect(results[0]).toMatchObject({ version: 1, state: 'completed', result: { version: 1, operationId, revisionId, state: 'open' } });
  expect(JSON.stringify(results[0])).not.toMatch(/note|discord|contributorId|keyId/);
  expect(outputs.every(output => output.headers.get('cache-control') === 'private, no-store')).toBe(true);
  expect(await db.prepare('SELECT count(*) AS total FROM reports').first()).toEqual({ total: 1 });
  expect(await db.prepare('SELECT count(*) AS total FROM revision_votes').first()).toEqual({ total: 0 });
  expect(await db.prepare('SELECT state FROM contribution_publications WHERE revision_id = ?').bind(revisionId).first()).toEqual({ state: 'published' });
  expect((await handle(await request({ ...contract.report, note: 'Changed' }, operationId))).status).toBe(409);
  expect((await handle(await request())).status).toBe(409);
  await resolveOpen();
  expect(await (await handle(await request(contract.report, operationId))).json()).toEqual(results[0]);
});

it('supports exactly the approved reasons and retains hostile markup as private inert plain text', async () => {
  for (const reason of ['spam', 'inaccurate', 'privacy', 'rights', 'other']) {
    const note = '<script>alert("private")</script>\nPlain text';
    const output = await handle(await request({ version: 1, reason, note }));
    expect(output.status).toBe(200);
    const result = await output.json() as { result: { reportId: string } };
    expect((await inspectModeration(db, 'report', result.result.reportId)).state).toMatchObject({ reason, note, state: 'open' });
    await resolveOpen();
  }
  for (const body of [{ version: 1, reason: 'dislike' }, { ...contract.report, vote: 'like' },
    { ...contract.report, note: 'x'.repeat(1001) }, { ...contract.report, note: '\u0000' }])
    expect((await handle(await request(body))).status).toBe(400);
});

it('requires counted exact-revision imports, non-self authorship, membership and active contributor keys', async () => {
  expect((await handle(await request(undefined, undefined, other))).status).toBe(403);
  expect((await handle(await request(undefined, undefined, await fixtureKey()))).status).toBe(403);
  expect((await handle(await request(), async () => 'ineligible' as never)).status).toBe(403);
  expect((await handle(await request(), async () => 'unavailable' as never)).status).toBe(503);
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'reporter'").run();
  try { expect((await handle(await request())).status).toBe(401); }
  finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'reporter'").run(); }
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'contributor-1'").run();
  try { expect((await handle(await request())).status).toBe(404); }
  finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'contributor-1'").run(); }
});

it('permits repeated resolved reports without a daily allowance while deduplicating open reports', async () => {
  const operationId = crypto.randomUUID();
  expect((await handle(await request(contract.report, operationId))).status).toBe(200);
  await resolveOpen();
  const original = await handle(await request()); expect(original.status).toBe(200);
  expect((await handle(await request())).status).toBe(409);
  await resolveOpen();
});

it('rejects tampering, method/size/query abuse and late quarantine without retaining partial reports', async () => {
  const signed = await request();
  expect((await handle(new Request(signed.url, { method: 'POST', headers: signed.headers, body: '{}' }))).status).toBe(401);
  expect((await handle(new Request(signed.url))).status).toBe(405);
  expect((await handle(await request(), undefined, { ...caps, requestBytes: 10 })).status).toBe(413);
  expect((await handle(await request(), undefined, { ...caps, maxQueries: 1 })).status).toBe(413);
  const race = new Proxy(db, { get(target, property) {
    if (property === 'batch') return async (statements: SqlStatement[]) => {
      if (statements.length === 3) await db.prepare("UPDATE contribution_publications SET state = 'quarantined' WHERE revision_id = ?").bind(revisionId).run();
      return target.batch(statements);
    };
    const value = Reflect.get(target, property); return typeof value === 'function' ? value.bind(target) : value;
  } });
  const operationId = crypto.randomUUID();
  try {
    const output = await contributorReport(await request(contract.report, operationId), race, 'community.example', now, async () => 'eligible', caps);
    expect(output.status).toBe(404);
    expect(await db.prepare("SELECT result FROM operations WHERE kind = 'report' AND operation_id = ?").bind(operationId).first()).toBeNull();
  } finally { await db.prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = ?").bind(revisionId).run(); }
});

it('records bounded local costs and wires native membership gates without production defaults', async context => {
  const observer = observeDownloadDatabase(db, 64);
  const output = await contributorReport(await request(), observer.db, 'community.example', now, async () => 'eligible', caps);
  expect(output.status).toBe(200);
  Object.assign(context.task.meta, { measurement: { scope: 'local report handler only', ...observer.snapshot() } });
  expect(observer.snapshot().rowsWritten).toBeLessThanOrEqual(32);
  await resolveOpen();
  const timestamp = Math.floor(Date.now() / 1000), limiter = { limit: vi.fn(async () => ({ success: true })) };
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ user: { id: '1554889314549243999' } })));
  const env = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
    REPORT_LIMITS: JSON.stringify(caps), DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token'};
  async function edge() {
    const signed = await request(undefined, undefined, key, timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  try {
    expect((await service.fetch(await edge(), env)).status).toBe(200);
    expect((await service.fetch(await edge(), { ...env, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect((await service.fetch(await edge(), { ...env, REPORT_LIMITS: undefined })).status).toBe(503);
    expect((await service.fetch(await edge(), { ...env, APP_ENV: 'production' })).status).toBe(503);
  } finally { vi.unstubAllGlobals(); }
});