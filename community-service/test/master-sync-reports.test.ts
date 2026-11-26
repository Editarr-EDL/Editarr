import { beforeAll, describe, expect, it } from 'vitest';
import { contributorSync } from '../src/http/sync';
import { contributorReport } from '../src/http/reports';
import { moderate } from '../src/admin/moderation';
import { contributor, support, type Contributor } from './helpers/submissions';
import { download, eligible, post, reply, submitWork } from './helpers/master';
import { registerKey } from './helpers/key-lifecycle';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

const sync = async (who: Contributor, value: unknown, overrides: Partial<typeof policy> = {}, at = now) =>
  reply(await contributorSync(await post(who, '/v1/sync/revisions', value, undefined, at), db, 'community.example', at, eligible,
    { policy: { ...policy, syncRunsPerDay: 2, syncMaxBatchesPerRun: 2, ...overrides } }));
const report = async (who: Contributor, value: unknown, operationId?: string) =>
  reply(await contributorReport(await post(who, '/v1/reports', value, operationId), db, 'community.example', now, eligible,
    { requestBytes: 8192, maxQueries: 64 }));
let alice: Contributor, bob: Contributor, carol: Contributor;
let current: string, changed: string, merged: string, quarantined: string, filterId: string;

beforeAll(async () => {
  await setupVersionCatalog([]);
  [alice, bob, carol] = await Promise.all([contributor(), contributor(), contributor()]);
  current = (await submitWork(alice, '400')).versionId;
  changed = (await submitWork(alice, '401')).versionId;
  await submitWork(bob, '401', { entries: [support('b', 600_000, 605_000)] });
  merged = (await submitWork(alice, '402', { version: { choice: 'alias', aliasId: 'extended' } })).versionId;
  const target = (await submitWork(alice, '402')).versionId;
  await db.prepare("UPDATE versions SET status = 'merged_into', merged_into = ? WHERE version_id = ?").bind(target, merged).run();
  quarantined = (await submitWork(alice, '403')).versionId;
  await db.prepare('UPDATE versions SET quarantined = 1 WHERE version_id = ?').bind(quarantined).run();
  const downloaded = await download(bob, current);
  filterId = downloaded.snapshot.filters[0].filterId;
});

describe('POST /v1/sync/revisions', () => {
  it('reports only changed, merged and unavailable versions in request order', async () => {
    const receipts = await db.prepare('SELECT count(*) AS n FROM download_receipts').first();
    const answer = await sync(alice, { version: 1, runId: 'run-1', items: [{ versionId: current, revision: 1 },
      { versionId: changed, revision: 1 }, { versionId: merged, revision: 1 }, { versionId: quarantined, revision: 1 },
      { versionId: 'v_unknown', revision: 3 }] });
    expect(answer.status).toBe(200);
    const target = (await db.prepare('SELECT merged_into FROM versions WHERE version_id = ?').bind(merged).first<{ merged_into: string }>())!.merged_into;
    expect(answer.body).toEqual({ version: 1, changed: [{ versionId: changed, status: 'changed', revision: 2 },
      { versionId: merged, status: 'merged', revision: null, mergedInto: target },
      { versionId: quarantined, status: 'unavailable', revision: null }, { versionId: 'v_unknown', status: 'unavailable', revision: null }],
    nextRunAllowedAt: now });
    expect(await db.prepare('SELECT count(*) AS n FROM download_receipts').first()).toEqual(receipts);
  });

  it('bounds batches per run, the run window and runs per installation per rolling day', async () => {
    const dave = await contributor();
    const body = (runId: string) => ({ version: 1, runId, items: [{ versionId: current, revision: 1 }] });
    expect((await sync(dave, body('a'), { syncRunsPerDay: 1 })).body).toEqual({ version: 1, changed: [], nextRunAllowedAt: now + 86_400 });
    expect((await sync(dave, body('a'), { syncRunsPerDay: 1 })).status).toBe(200);
    const batches = await sync(dave, body('a'), { syncRunsPerDay: 1 });
    expect([batches.status, batches.body, batches.headers.get('retry-after')]).toEqual([429, { error: { code: 'sync_rate_limited' } }, '86400']);
    const fresh = await sync(dave, body('b'), { syncRunsPerDay: 1 }, now + 100);
    expect([fresh.status, fresh.headers.get('retry-after')]).toEqual([429, '86300']);
    expect((await sync(dave, body('b'), { syncRunsPerDay: 1 }, now + 86_401)).status).toBe(200);
    const late = await sync(dave, body('b'), { syncRunsPerDay: 1, syncRunWindowSeconds: 10 }, now + 86_412);
    expect(late.status).toBe(429);
    const sibling = await registerKey(db, dave.contributorId as ReturnType<typeof crypto.randomUUID>);
    expect((await sync(sibling as Contributor, body('a'), { syncRunsPerDay: 1 }, now + 86_412)).status).toBe(200);
  });

  it.each([{ version: 1, runId: 'r', items: [] }, { version: 1, runId: 'bad id', items: [{ versionId: 'v', revision: 1 }] },
    { version: 1, runId: 'r', items: [{ versionId: 'v', revision: 1 }, { versionId: 'v', revision: 2 }] },
    { version: 1, runId: 'r', items: Array.from({ length: 501 }, (_, index) => ({ versionId: `v${index}`, revision: 1 })) },
    { version: 1, runId: 'r', items: [{ versionId: 'v', revision: -1 }] }, { version: 1, items: [{ versionId: 'v', revision: 1 }] }])(
    'rejects an invalid request with invalid_sync', async value => {
      expect(await sync(carol, value)).toMatchObject({ status: 400, body: { error: { code: 'invalid_sync' } } });
    });
});

describe('POST /v1/reports', () => {
  it('files one open report per target for receipt holders and replays retries', async () => {
    const operationId = crypto.randomUUID();
    const value = { version: 1, target: { type: 'filter', id: filterId }, reason: 'inaccurate', note: 'Wrong scene' };
    const filed = await report(bob, value, operationId);
    expect(filed.status).toBe(200);
    expect(filed.body).toMatchObject({ version: 1, state: 'completed', result: { version: 1, operationId, state: 'open' } });
    expect((await report(bob, value, operationId)).body).toEqual(filed.body);
    expect(await report(bob, value)).toMatchObject({ status: 409, body: { error: { code: 'report_already_open' } } });
    expect(await db.prepare('SELECT target_type, target_id, reason, note, state FROM reports WHERE report_id = ?')
      .bind(filed.body.result.reportId).first()).toEqual({ target_type: 'filter', target_id: filterId, reason: 'inaccurate',
      note: 'Wrong scene', state: 'open' });
    expect((await report(bob, { version: 1, target: { type: 'version', id: current }, reason: 'spam' })).status).toBe(200);
    await moderate(db, 'operator', { version: 1, actionId: crypto.randomUUID(), command: 'resolve_report', resource: 'report',
      targetId: filed.body.result.reportId, expectedRevision: 0, reason: 'Reviewed' }, now);
    expect((await report(bob, value)).status).toBe(200);
  });

  it('requires a receipt for the version, or one whose snapshot contained the filter', async () => {
    for (const target of [{ type: 'version', id: current }, { type: 'filter', id: filterId }, { type: 'version', id: 'v_unknown' }])
      expect(await report(carol, { version: 1, target, reason: 'other' })).toMatchObject({ status: 403,
        body: { error: { code: 'report_ineligible' } } });
    const target = (await db.prepare('SELECT merged_into FROM versions WHERE version_id = ?').bind(merged).first<{ merged_into: string }>())!.merged_into;
    await db.prepare(`INSERT INTO master_revisions (version_id, revision, snapshot_digest, generated_at, snapshot_bytes)
      VALUES (?, 99, ?, 1, NULL)`).bind(merged, 'a'.repeat(64)).run();
    await db.prepare(`INSERT INTO download_receipts VALUES (?, ?, ?, 99, ?, 'key', 1)`).bind('b'.repeat(64), carol.contributorId, merged, 'a'.repeat(64)).run();
    expect((await report(carol, { version: 1, target: { type: 'version', id: target }, reason: 'other' })).status).toBe(200);
  });

  it.each([{ version: 1, target: { type: 'work', id: 'w' }, reason: 'spam' }, { version: 1, target: { type: 'filter', id: 'f' }, reason: 'rude' },
    { version: 1, target: { type: 'filter', id: 'f' }, reason: 'spam', note: 'x'.repeat(1001) },
    { version: 1, target: { type: 'filter', id: 'f' }, reason: 'spam', extra: true }])('rejects %j with invalid_report', async value => {
    expect(await report(bob, value)).toMatchObject({ status: 400, body: { error: { code: 'invalid_report' } } });
  });
});
