import { beforeAll, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { contributor, type Contributor } from './helpers/submissions';
import { submitWork } from './helpers/master';
import { db, now, setupVersionCatalog } from './helpers/versions';

const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }) };
let jwt: string, resolver: JWTVerifyGetKey, author: Contributor;

beforeAll(async () => {
  await setupVersionCatalog([]);
  author = await contributor();
  await submitWork(author, '700');
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});
const handle = (path: string, token = jwt) => operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/dashboard/${path}`,
  { headers: { 'Cf-Access-Jwt-Assertion': token } }), settings, resolver, new Date(now * 1000));

const authorVersion = async () => (await db.prepare('SELECT version_id FROM submissions WHERE contributor_id = ?')
  .bind(author.contributorId).first<{ version_id: string }>())!.version_id;

it('protects every read and exposes no Discord IDs, keys, payloads or operation bodies', async () => {
  const versionId = await authorVersion();
  for (const path of ['overview', 'contributors', `contributors/${author.contributorId}/keys`,
    `contributors/${author.contributorId}/usage`, 'operations', 'audit', `versions/${versionId}/filters`, `versions/${versionId}/submissions`]) {
    expect((await handle(path, '')).status).toBe(401);
    const response = await handle(path);
    expect(response.status).toBe(200);
    const body = await response.text();
    for (const forbidden of ['discord_user_id', 'public_key', 'request_digest', 'artifact_bytes', 'result', 'digest', 'match_keys_json']) expect(body).not.toContain(`"${forbidden}`);
  }
  expect((await handle('contributions')).status).toBe(404);
});

it('reports stored submission bytes and keeps provider values unavailable', async () => {
  const value = await (await handle('overview')).json() as Record<string, any>;
  expect(value.storage).toMatchObject({ source: 'artifact_storage_usage', providerVerified: false });
  expect(value.storage.globalBytes).toBeGreaterThan(0);
  for (const key of ['databaseSize', 'providerUsage', 'backup']) expect(value[key]).toEqual({ source: 'unavailable', observedAt: null, value: null, providerVerified: false });
  expect(value.observations).toEqual([]);
  const usage = await (await handle(`contributors/${author.contributorId}/usage`)).json() as Record<string, any>;
  expect(usage.storage.bytes).toBeGreaterThan(0);
});

it('lists version, alias and moderation actions in one audit page', async () => {
  await db.prepare(`INSERT INTO catalog_moderation_actions (action_id, actor, resource, target_id, command, expected_revision, reason,
    request_json, outcome, error_code, created_at) VALUES ('a-catalog', 'operator', 'version:v', 'v', 'confirm_version', 0, 'r', '{}',
    'denied', 'resource_not_found', 1)`).run();
  await db.prepare(`INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision, reason,
    request_json, outcome, error_code, created_at) VALUES ('b-account', 'operator', 'contributor:c', 'c', 'suspend', 0, 'r', '{}',
    'denied', 'resource_not_found', 2)`).run();
  const page = await (await handle('audit')).json() as { items: { actionId: string; command: string }[] };
  expect(page.items.map(item => [item.actionId, item.command])).toEqual([['a-catalog', 'confirm_version'], ['b-account', 'suspend']]);
});

it('pages a version\'s filters with counts only and its submissions without payloads', async () => {
  const versionId = await authorVersion();
  const filters = await (await handle(`versions/${versionId}/filters`)).json() as { items: Record<string, unknown>[]; nextCursor: string | null };
  expect(filters.items.length).toBeGreaterThan(0);
  expect(Object.keys(filters.items[0])).toEqual(['filterId', 'state', 'quarantined', 'category', 'subcategory', 'severity', 'action',
    'startMs', 'endMs', 'confidence', 'contributorCount', 'attestations', 'upvotes', 'downvotes', 'disputes', 'served']);
  expect(filters.items[0].attestations).toBeTypeOf('number');
  const submissions = await (await handle(`versions/${versionId}/submissions`)).json() as { items: Record<string, unknown>[] };
  expect(submissions.items).toEqual([expect.objectContaining({ contributorId: author.contributorId, quarantined: 0 })]);
  expect(JSON.stringify(submissions)).not.toContain('digest');
  expect((await handle('versions/v_missing/filters')).status).toBe(404);
  expect((await handle(`versions/${versionId}/filters?after=!`)).status).toBe(400);
});

it('pages more than 20 filters and submissions, keeps registry-only filters and counts filters exactly', async () => {
  const source = await authorVersion(), seeded = 'v_paged';
  await db.prepare(`INSERT INTO versions (version_id, work_id, alias_id, label_text, status, merged_into, runtime_ms, trust, revision,
    reference_submission_id, quarantined, created_seq, created_at) SELECT ?, work_id, alias_id, 'Paged', 'candidate', NULL, runtime_ms, trust, 1,
    NULL, 0, (SELECT max(created_seq) + 1 FROM versions), created_at FROM versions WHERE version_id = ?`).bind(seeded, source).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO version_acceptances (submission_id, version_id, contributor_id, accepted_seq, tier, founder, duration_ms, quarantined, merged_from, accepted_at)
    SELECT printf('s_paged_%02d', i), ?, a.contributor_id, (SELECT max(accepted_seq) FROM version_acceptances) + i, NULL, 0, a.duration_ms, 0, NULL, a.accepted_at
    FROM n, version_acceptances a WHERE a.version_id = ?`).bind(seeded, source).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO submissions (submission_id, digest, contributor_id, installation_id, key_id, version_id, accepted_seq, tier, artifact_bytes,
      manifest_bytes, envelope_bytes, match_keys_json, alignment_hint_json, fit_json, state, created_at)
    SELECT printf('s_paged_%02d', i), printf('%064x', i), s.contributor_id, s.installation_id, s.key_id, ?,
      (SELECT accepted_seq FROM version_acceptances WHERE submission_id = printf('s_paged_%02d', i)), NULL, s.artifact_bytes, s.manifest_bytes,
      s.envelope_bytes, s.match_keys_json, NULL, NULL, 'counted', s.created_at FROM n, submissions s WHERE s.version_id = ?`).bind(seeded, source).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 23)
    INSERT INTO filter_registry (filter_id, version_id, created_seq, incumbent_subcategory, incumbent_action, state, merged_into, quarantined)
    SELECT printf('f_paged_%02d', i), ?, i, NULL, NULL, CASE WHEN i = 23 THEN 'retired' ELSE 'active' END, NULL, 0 FROM n`).bind(seeded).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO master_filters (filter_id, version_id, category, subcategory, severity, action, start_ms, end_ms, spread_start_ms, spread_end_ms,
      confidence, contributor_count, attestations, upvotes, downvotes, disputes, coverage_negatives, low_confidence, served,
      subcategory_tally_json, severity_tally_json, action_tally_json)
    SELECT printf('f_paged_%02d', i), ?, 'violence', 'gore', 'high', 'skip', i * 1000, i * 1000 + 500, 0, 0, 0.5, 1, i, 0, 0, 0, 0, 0,
      CASE WHEN i <= 15 THEN 1 ELSE 0 END, '{}', '{}', '{}' FROM n`).bind(seeded).run();
  type Page = { items: Record<string, unknown>[]; nextCursor: string | null };
  const read = async (path: string) => await (await handle(path)).json() as Page;
  const filters = await read(`versions/${seeded}/filters`);
  expect(filters.items).toHaveLength(20);
  const rest = await read(`versions/${seeded}/filters?after=${encodeURIComponent(filters.nextCursor!)}`);
  expect(rest.nextCursor).toBeNull();
  expect(rest.items.map(item => item.filterId)).toEqual(['f_paged_21', 'f_paged_22', 'f_paged_23']);
  expect(rest.items[2]).toMatchObject({ state: 'retired', category: null, confidence: null, attestations: null, served: null });
  expect(rest.items[1]).toMatchObject({ attestations: 22, served: 0 });
  const submissions = await read(`versions/${seeded}/submissions`);
  expect(submissions.items).toHaveLength(20);
  const tail = await read(`versions/${seeded}/submissions?after=${encodeURIComponent(submissions.nextCursor!)}`);
  expect(tail).toMatchObject({ nextCursor: null, items: [{ submissionId: 's_paged_21' }, { submissionId: 's_paged_22' }] });
  const list = await (await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/versions?status=candidate`,
    { headers: { 'Cf-Access-Jwt-Assertion': jwt } }), settings, resolver, new Date(now * 1000))).json() as { versions: Record<string, unknown>[] };
  expect(list.versions.find(item => item.versionId === seeded)).toMatchObject({ filterCount: 22, servedFilterCount: 15 });
});
