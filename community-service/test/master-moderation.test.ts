import { beforeAll, describe, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { contributor, support, type Contributor } from './helpers/submissions';
import { download, eligible, post, reply, submitWork } from './helpers/master';
import { contributorVotes } from '../src/http/votes';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

const env = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  PUBLIC_ORIGIN: 'https://community.example', ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app',
  OPERATOR_SUBJECTS: '["operator-subject"]', MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }),
  MASTER_POLICY: JSON.stringify(policy) };
let jwt: string, resolver: JWTVerifyGetKey, alice: Contributor, bob: Contributor, carol: Contributor;

async function admin(path: string, body?: unknown, settings: Record<string, unknown> = env) {
  return reply(await operatorService(new Request(`${env.OPERATOR_ORIGIN}${path}`, { method: body ? 'POST' : 'GET', headers: {
    'Cf-Access-Jwt-Assertion': jwt, Origin: env.OPERATOR_ORIGIN, 'X-Community-Operator': '1', 'Content-Type': 'application/json',
    'Sec-Fetch-Site': 'same-origin' }, ...(body ? { body: JSON.stringify(body) } : {}) }), settings, resolver, new Date(now * 1000)));
}
/** Apply one action at the resource's current moderation revision. */
async function act(command: string, resource: string, targetId: string, extra: Record<string, unknown> = {}) {
  const key = resource === 'vote' ? `${targetId}/${extra.contributorId}` : encodeURIComponent(targetId);
  const current = await admin(`/admin/v1/moderation/${resource}/${key}`);
  return admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command, resource, targetId,
    expectedRevision: current.body.revision ?? 0, reason: 'Reviewed evidence', ...extra });
}
const content = async (versionId: string) => {
  const row = await db.prepare(`SELECT revision, snapshot_bytes FROM master_revisions WHERE version_id = ? ORDER BY revision DESC LIMIT 1`)
    .bind(versionId).first<{ revision: number; snapshot_bytes: ArrayBuffer }>();
  const { revision: _revision, generatedAt: _generated, ...served } = JSON.parse(Buffer.from(row!.snapshot_bytes).toString());
  return { revision: row!.revision, served: served as { filters: { filterId: string; support: { attestations: number; upvotes: number } }[] } };
};
const revision = async (versionId: string) => (await db.prepare('SELECT revision FROM versions WHERE version_id = ?')
  .bind(versionId).first<{ revision: number }>())!.revision;

beforeAll(async () => {
  await setupVersionCatalog([]);
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: env.ACCESS_ISSUER, aud: env.ACCESS_AUDIENCE, exp: now + 60, iat: now - 60,
    type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
  [alice, bob, carol] = await Promise.all([contributor(), contributor(), contributor()]);
});

describe('master-set moderation (§7.8)', () => {
  it('quarantines and restores a filter, recomputing reversibly with audited revisions', async () => {
    const versionId = (await submitWork(alice, '500', { entries: [support('a', 600_000, 605_000), support('b', 900_000, 905_000)] })).versionId;
    const before = await content(versionId), filterId = before.served.filters[0].filterId;
    const quarantined = await act('quarantine', 'filter', filterId);
    expect(quarantined.body).toMatchObject({ resource: `filter:${filterId}`, revision: 1, outcome: 'applied' });
    expect((await content(versionId)).served.filters.map(filter => filter.filterId)).not.toContain(filterId);
    expect(await act('quarantine', 'filter', filterId)).toMatchObject({ status: 403, body: { error: { code: 'moderation_ineligible' } } });
    expect((await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command: 'restore', resource: 'filter',
      targetId: filterId, expectedRevision: 0, reason: 'Stale' })).body).toEqual({ error: { code: 'moderation_conflict' } });
    await act('restore', 'filter', filterId);
    const after = await content(versionId);
    expect(after.served).toEqual(before.served);
    expect(after.revision).toBe(before.revision + 2);
    expect(await act('quarantine', 'filter', 'f_missing')).toMatchObject({ status: 404, body: { error: { code: 'resource_not_found' } } });
  });

  it('quarantines attestations, submissions and contributors and restores the same served content', async () => {
    const versionId = (await submitWork(alice, '501')).versionId;
    const first = await submitWork(bob, '501', { entries: [support('b1', 600_100, 605_100)] });
    const second = await submitWork(bob, '501', { entries: [support('b2', 600_100, 605_100), support('b3', 3_000_000, 3_005_000)] });
    const baseline = await content(versionId);
    expect(baseline.served.filters.map(filter => filter.support.attestations)).toEqual([2, 1]);
    await act('quarantine', 'attestation', `${second.submissionId}:b2`);
    expect((await content(versionId)).served.filters[0].support.attestations).toBe(1);
    await act('restore', 'attestation', `${second.submissionId}:b2`);
    expect((await content(versionId)).served).toEqual(baseline.served);
    await act('quarantine', 'submission', second.submissionId);
    expect(await db.prepare('SELECT state FROM submissions WHERE submission_id = ?').bind(first.submissionId).first()).toEqual({ state: 'counted' });
    expect((await content(versionId)).served.filters).toHaveLength(1);
    await act('restore', 'submission', second.submissionId);
    expect((await content(versionId)).served).toEqual(baseline.served);
    await act('quarantine', 'contributor', bob.contributorId);
    expect((await content(versionId)).served.filters.map(filter => filter.support.attestations)).toEqual([1]);
    expect((await admin(`/admin/v1/moderation/contributor/${bob.contributorId}`)).body.state).toMatchObject({ quarantined: 1, state: 'active' });
    await act('restore', 'contributor', bob.contributorId);
    expect((await content(versionId)).served).toEqual(baseline.served);
    const inspected = await admin(`/admin/v1/moderation/attestation/${encodeURIComponent(`${second.submissionId}:b2`)}`);
    expect(inspected.body).toMatchObject({ resource: `attestation:${second.submissionId}:b2`, revision: 2, state: { quarantined: 0 } });
  });

  it('freezes a quarantined version and advances on restore only when content changed', async () => {
    const versionId = (await submitWork(alice, '502')).versionId;
    await act('quarantine', 'version', versionId);
    const frozen = await revision(versionId);
    await submitWork(bob, '502', { entries: [support('x', 600_000, 605_000)] });
    expect(await revision(versionId)).toBe(frozen);
    await act('restore', 'version', versionId);
    expect(await revision(versionId)).toBe(frozen + 1);
    await act('quarantine', 'version', versionId);
    await act('restore', 'version', versionId);
    expect(await revision(versionId)).toBe(frozen + 1);
  });

  it('excludes and restores a vote', async () => {
    const versionId = (await submitWork(alice, '503')).versionId;
    const { snapshot, receiptId } = await download(carol, versionId);
    const filterId = snapshot.filters[0].filterId;
    await contributorVotes(await post(carol, `/v1/filters/${filterId}/votes`, { version: 1, vote: 'up', receiptId }, `vote-${crypto.randomUUID()}`), db,
      'community.example', now, eligible, { policy, limits: { requestBytes: 8192, maxQueries: 64, votesPerHour: 1000 } });
    const upvotes = async () => (await content(versionId)).served.filters[0].support.upvotes;
    expect(await upvotes()).toBe(1);
    await act('exclude_vote', 'vote', filterId, { contributorId: carol.contributorId });
    expect(await upvotes()).toBe(0);
    await act('restore_vote', 'vote', filterId, { contributorId: carol.contributorId });
    expect(await upvotes()).toBe(1);
  });
});

describe('catalog moderation recompute hook', () => {
  it('merges submissions, votes and receipt eligibility into the target', async () => {
    const target = (await submitWork(alice, '504')).versionId;
    const source = (await submitWork(bob, '504', { version: { choice: 'alias', aliasId: 'extended' },
      entries: [support('s1', 1_500_000, 1_505_000)] })).versionId;
    const { snapshot, receiptId } = await download(carol, source);
    const sourceFilter = snapshot.filters[0].filterId;
    const votes = (body: unknown) => post(carol, `/v1/filters/${sourceFilter}/votes`, body, `vote-${crypto.randomUUID()}`).then(async request =>
      reply(await contributorVotes(request, db, 'community.example', now, eligible, { policy, limits: { requestBytes: 8192, maxQueries: 64, votesPerHour: 1000 } })));
    expect((await votes({ version: 1, vote: 'up', receiptId })).status).toBe(200);
    const merged = await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command: 'merge_version',
      resource: 'version', targetId: source, intoVersionId: target, expectedRevision: 0, reason: 'Same cut' });
    expect(merged.body).toMatchObject({ outcome: 'applied' });
    const served = (await content(target)).served.filters;
    expect(served.map(filter => filter.filterId)).toContain(sourceFilter);
    expect(served.find(filter => filter.filterId === sourceFilter)!.support.upvotes).toBe(1);
    expect(await db.prepare('SELECT count(*) AS n FROM submissions WHERE version_id = ?').bind(source).first()).toEqual({ n: 0 });
    expect(await db.prepare('SELECT count(*) AS n FROM master_filters WHERE version_id = ?').bind(source).first()).toEqual({ n: 0 });
    expect((await votes({ version: 1, vote: 'down', receiptId })).body.result).toMatchObject({ versionId: target, vote: 'down' });
  });

  it('relabels an approved alias at the advanced revision without a second bump, and confirms candidates', async () => {
    const created = await submitWork(alice, '505', { version: { choice: 'other', text: 'Fan Restoration' } });
    expect(await revision(created.versionId)).toBe(1);
    const suggestion = created.aliasSuggestion.suggestionId;
    const approved = await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command: 'approve_alias',
      resource: 'alias_suggestion', targetId: suggestion, newAliasId: 'fan_restoration', label: 'Fan Restoration',
      expectedRevision: 0, reason: 'Common cut' });
    expect(approved.body).toMatchObject({ outcome: 'applied' });
    expect(await revision(created.versionId)).toBe(2);
    const relabeled = await content(created.versionId);
    expect(relabeled.revision).toBe(2);
    expect(relabeled.served).toMatchObject({ versionInfo: { aliasId: 'fan_restoration', label: 'Fan Restoration', status: 'candidate' } });
    const current = (await admin(`/admin/v1/moderation/version/${created.versionId}`)).body.revision;
    expect(current).toBe(1);
    await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command: 'confirm_version', resource: 'version',
      targetId: created.versionId, expectedRevision: current, reason: 'Verified' });
    expect(await content(created.versionId)).toMatchObject({ revision: 3, served: { versionInfo: { status: 'confirmed' } } });
  });
});

describe('POST /admin/v1/recompute', () => {
  it('recomputes listed versions under a changed policy and pages through all versions', async () => {
    const versionId = (await submitWork(alice, '506')).versionId;
    const action = (versionIds: unknown, actionId: string = crypto.randomUUID(), settings: Record<string, unknown> = env) =>
      admin('/admin/v1/recompute', { version: 1, actionId, versionIds, reason: 'Policy change' }, settings);
    expect((await action([versionId])).body.versions).toEqual([{ versionId, revision: 1, changed: false }]);
    const strict = { ...env, MASTER_POLICY: JSON.stringify({ ...policy, confidenceFloor: 0.9 }) };
    const actionId = crypto.randomUUID();
    expect((await action([versionId], actionId, strict)).body.versions).toEqual([{ versionId, revision: 2, changed: true }]);
    expect((await content(versionId)).served.filters).toEqual([]);
    expect((await action([versionId], actionId, strict)).body.versions).toEqual([{ versionId, revision: 2, changed: false }]);
    expect(await action(['other'], actionId)).toMatchObject({ status: 409, body: { error: { code: 'action_conflict' } } });
    expect(await action(['v_missing'])).toMatchObject({ status: 404, body: { error: { code: 'resource_not_found' } } });
    expect(await action('everything')).toMatchObject({ status: 400, body: { error: { code: 'invalid_moderation' } } });
    const all = await action('all');
    expect(all.body).toEqual({ version: 1, jobId: expect.any(String), complete: true, nextAfter: null });
    expect((await admin('/admin/v1/recompute', { version: 1, actionId: crypto.randomUUID(), versionIds: 'all', after: 'zzzz',
      reason: 'Continue' })).body).toMatchObject({ complete: true, nextAfter: null });
    expect((await admin('/admin/v1/recompute', { version: 1, actionId: crypto.randomUUID(), versionIds: [versionId], after: 'v',
      reason: 'Continue' })).status).toBe(400);
    expect((await content(versionId)).served.filters).toHaveLength(1);
    expect(await db.prepare("SELECT count(*) AS n FROM moderation_actions WHERE command = 'recompute'").first()).toEqual({ n: 4 });
  });
});
