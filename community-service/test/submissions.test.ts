import { beforeAll, describe, expect, it, vi } from 'vitest';
import fixture from './fixtures/community-v3/artifact-v3.json';
import { tunnelActor } from '../src/runtime/environment.mjs';
import service from '../src/index';
import { contributorSubmission } from '../src/http/submissions';
import { DEFAULT_SUBMISSION_LIMITS } from '../src/submissions/input';
import { publicKeyId } from '../src/identity/rotation-proof';
import type { SqlDatabase } from '../src/runtime/contracts';
import { artifact, body, contributor, encode, hex, nonce, service as serviceOrigin, submitRequest, support, type Contributor } from './helpers/submissions';
import { recomputeVersion } from '../src/master/recompute';
import { signedPost } from './helpers/key-lifecycle';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

const eligible = async () => 'eligible' as const;
const handle = (request: Request, database: SqlDatabase = db, overrides: Partial<typeof policy> = {}) =>
  contributorSubmission(request, database, 'community.example', now, eligible, { policy: { ...policy, ...overrides }, limits: DEFAULT_SUBMISSION_LIMITS });
type Reply = { status: number; body: { result?: Record<string, any>; error?: { code: string } }; headers: Headers };
async function submit(author: Contributor, value = artifact(author), options: { database?: SqlDatabase; text?: string; operationId?: string;
  policy?: Partial<typeof policy> } = {}): Promise<Reply> {
  const response = await handle(await submitRequest(author, await encode(value, author), options), options.database, options.policy);
  return { status: response.status, body: await response.json() as Reply['body'], headers: response.headers };
}
const count = async (sql: string, ...values: unknown[]) => (await db.prepare(sql).bind(...values).first<{ n: number }>())!.n;
let alice: Contributor, bob: Contributor, carol: Contributor;

beforeAll(async () => {
  await setupVersionCatalog([]);
  [alice, bob, carol] = await Promise.all([contributor(), contributor(), contributor()]);
});

describe('POST /v1/submissions', () => {
  it('creates a confirmed version, stores raw bytes once and serves revision 1', async () => {
    const value = artifact(alice, { work: { ...artifact(alice).work, tmdb: '100' } });
    const encoded = await encode(value, alice);
    const response = await handle(await submitRequest(alice, encoded));
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
    const { result } = await response.json() as { result: Record<string, any> };
    expect(result).toMatchObject({ version: 1, operationId: `submit-${value.submissionId}`, submissionId: value.submissionId,
      versionStatus: 'confirmed', disposition: 'created_confirmed', tier: null, revision: 1, supersedes: null,
      outcome: { newFilters: 1, supports: 1, disputes: 0, coverageNegatives: 0, ignored: 0, unassigned: 0 }, aliasSuggestion: null });
    expect(result.submissionDigest).toBe(Buffer.from(await crypto.subtle.digest('SHA-256', encoded.artifactBytes as BufferSource)).toString('hex'));
    const stored = await db.prepare('SELECT artifact_bytes, manifest_bytes, envelope_bytes, state FROM submissions WHERE submission_id = ?')
      .bind(value.submissionId).first<{ artifact_bytes: ArrayBuffer; manifest_bytes: ArrayBuffer; envelope_bytes: ArrayBuffer; state: string }>();
    expect(Buffer.from(stored!.artifact_bytes).equals(encoded.artifactBytes)).toBe(true);
    expect(Buffer.from(stored!.manifest_bytes).equals(encoded.manifestBytes)).toBe(true);
    expect(Buffer.from(stored!.envelope_bytes).equals(encoded.envelopeBytes)).toBe(true);
    expect(stored!.state).toBe('counted');
    expect(await count('SELECT count(*) AS n FROM master_filters WHERE version_id = ? AND served = 1', result.versionId)).toBe(1);
    expect(await count('SELECT count(*) AS n FROM master_revisions WHERE version_id = ? AND revision = 1', result.versionId)).toBe(1);
    expect(await count('SELECT count(*) AS n FROM submission_entries WHERE submission_id = ? AND weight = 1', value.submissionId)).toBe(1);
    expect(await count("SELECT bytes AS n FROM artifact_storage_usage WHERE scope_id = 'contributor:' || ?", alice.contributorId))
      .toBe(encoded.artifactBytes.length + encoded.manifestBytes.length + encoded.envelopeBytes.length);
  });

  it('attaches at tier 2, supersedes a contributor’s earlier submission and advances the revision', async () => {
    const work = { ...artifact(alice).work, tmdb: '101' };
    const first = await submit(alice, artifact(alice, { work }));
    const second = await submit(bob, artifact(bob, { work, entries: [support('b1', 600_200, 605_100, { severity: 'high' })] }));
    expect(second.body.result).toMatchObject({ versionId: first.body.result!.versionId, disposition: 'attached', tier: '2', revision: 2,
      supersedes: null, outcome: { newFilters: 0, supports: 1 } });
    const again = await submit(bob, artifact(bob, { work, entries: [support('b2', 600_200, 605_100), support('b3', 900_000, 905_000)] }));
    expect(again.body.result).toMatchObject({ supersedes: second.body.result!.submissionId, revision: 3, outcome: { newFilters: 1, supports: 2 } });
    expect(await db.prepare('SELECT state FROM submissions WHERE submission_id = ?').bind(second.body.result!.submissionId).first())
      .toEqual({ state: 'superseded' });
    const versionId = first.body.result!.versionId as string;
    const snapshot = async () => (await db.prepare('SELECT snapshot_bytes FROM master_revisions WHERE version_id = ? AND revision = 3')
      .bind(versionId).first<{ snapshot_bytes: ArrayBuffer }>())!.snapshot_bytes;
    const before = Buffer.from(await snapshot());
    const recomputed = await recomputeVersion(db, versionId, { service: serviceOrigin, policy, now: now + 60 });
    expect([recomputed.revision, recomputed.changed]).toEqual([3, false]);
    expect(Buffer.from(await snapshot()).equals(before)).toBe(true);
    expect(await count('SELECT count(*) AS n FROM master_revisions WHERE version_id = ?', versionId)).toBe(3);
  });

  it('creates a candidate and an alias suggestion for unresolved Other text', async () => {
    const reply = await submit(carol, artifact(carol, { work: { ...artifact(carol).work, tmdb: '102' }, version: { choice: 'other', text: 'Fan Edit' } }));
    expect(reply.body.result).toMatchObject({ disposition: 'created_candidate', versionStatus: 'candidate',
      aliasSuggestion: { text: 'Fan Edit', state: 'pending' } });
  });

  it('replays an identical retry and refuses different bytes for the same submission ID', async () => {
    const value = artifact(alice, { work: { ...artifact(alice).work, tmdb: '103' } });
    const encoded = await encode(value, alice);
    const first = await handle(await submitRequest(alice, encoded));
    const retry = await handle(await submitRequest(alice, encoded));
    expect(await retry.json()).toEqual(await first.json());
    const changed = await encode({ ...value, createdAt: value.createdAt + 1 }, alice);
    const conflict = await handle(await submitRequest(alice, changed));
    expect([conflict.status, await conflict.json()]).toEqual([409, { error: { code: 'submission_conflict' } }]);
    const stolen = await encode({ ...value, author: { contributorId: bob.contributorId, keyId: bob.keyId } }, bob);
    expect((await handle(await submitRequest(bob, stolen))).status).toBe(409);
    expect(await count('SELECT count(*) AS n FROM submissions WHERE submission_id = ?', value.submissionId)).toBe(1);
  });

  it.each([
    ['a mismatched idempotency key', async () => submit(alice, undefined, { operationId: 'other-key' }), 400, 'invalid_submission'],
    ['a malformed body', async () => submit(alice, undefined, { text: '{"version":1}' }), 400, 'invalid_submission'],
    ['an unknown alias', async () => submit(alice, artifact(alice, { version: { choice: 'alias', aliasId: 'imax' } })), 422, 'unknown_alias'],
    ['no provider identifier', async () => submit(alice, artifact(alice, { work: { ...artifact(alice).work, tmdb: null } })), 422, 'work_identity_required'],
    ['an unknown subcategory', async () => submit(alice, artifact(alice, { entries: [support('x', 1, 2, { subcategory: 'swearing' })] })), 422, 'invalid_taxonomy'],
    ['bounds past the duration', async () => submit(alice, artifact(alice, { entries: [support('x', 1, 9_000_000)] })), 422, 'invalid_bounds'],
    ['another author', async () => submit(alice, { ...artifact(alice), author: { contributorId: bob.contributorId, keyId: alice.keyId } }), 422, 'key_mismatch'],
  ])('refuses %s', async (_name, run, status, code) => {
    const before = await count('SELECT count(*) AS n FROM submissions');
    const reply = await run();
    expect([reply.status, reply.body]).toEqual([status, { error: { code } }]);
    expect(await count('SELECT count(*) AS n FROM submissions')).toBe(before);
  });

  it('refuses uploads from a quarantined contributor and past the daily limit', async () => {
    const dave = await contributor();
    await db.prepare('UPDATE contributors SET quarantined = 1 WHERE contributor_id = ?').bind(dave.contributorId).run();
    expect((await submit(dave)).body).toEqual({ error: { code: 'upload_suspended' } });
    await db.prepare('UPDATE contributors SET quarantined = 0 WHERE contributor_id = ?').bind(dave.contributorId).run();
    expect((await submit(dave, undefined, { policy: { submissionsPerDay: 1 } })).status).toBe(200);
    const limited = await submit(dave, undefined, { policy: { submissionsPerDay: 1 } });
    expect([limited.status, limited.body]).toEqual([429, { error: { code: 'rate_limited' } }]);
    expect(Number(limited.headers.get('retry-after'))).toBeGreaterThan(0);
  });

  it('refuses retired and revoked signing keys, oversized bodies and over-long submission IDs', async () => {
    for (const state of ['retired', 'revoked']) {
      const frank = await contributor();
      const request = await submitRequest(frank, await encode(artifact(frank), frank));
      await db.prepare('UPDATE installation_keys SET state = ?, retired_at = 1 WHERE key_id = ?').bind(state, frank.keyId).run();
      const response = await handle(request);
      expect([response.status, await response.json()]).toEqual([401, { error: { code: 'key_inactive' } }]);
    }
    const oversized = await handle(await submitRequest(alice, await encode(artifact(alice), alice),
      { text: 'x'.repeat(DEFAULT_SUBMISSION_LIMITS.requestBytes + 1) }));
    expect([oversized.status, await oversized.json()]).toEqual([413, { error: { code: 'body_too_large' } }]);
    const encoded = await encode(artifact(alice), alice);
    const long = await handle(await submitRequest(alice, encoded, { text: body(encoded, 's'.repeat(122)), operationId: 'submit-long' }));
    expect([long.status, await long.json()]).toEqual([400, { error: { code: 'invalid_submission' } }]);
    expect((await submit(alice, artifact(alice, { submissionId: 's'.repeat(121) }))).status).toBe(200);
  });

  it('enforces the daily limit inside the batch for concurrent uploads', async () => {
    const gina = await contributor();
    const limit = { submissionsPerDay: 1 };
    const replies = await Promise.all([submit(gina, undefined, { policy: limit }), submit(gina, undefined, { policy: limit })]);
    expect(replies.map(item => item.status).sort()).toEqual([200, 429]);
    expect(await count('SELECT count(*) AS n FROM submissions WHERE contributor_id = ?', gina.contributorId)).toBe(1);
  });

  it('replans after a concurrent catalog write and commits the submission exactly once', async () => {
    const work = { ...artifact(alice).work, tmdb: '104' };
    const seeded = await submit(alice, artifact(alice, { work }));
    let raced = false;
    const racing = new Proxy(db, { get(target, property) {
      if (property === 'batch') return async (statements: unknown[]) => {
        if (!raced && statements.length > 5) {
          raced = true;
          await target.prepare('UPDATE works SET catalog_seq = catalog_seq + 1 WHERE work_id = ?').bind(seeded.body.result!.workId).run();
        }
        return target.batch(statements as Parameters<typeof target.batch>[0]);
      };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
    const reply = await submit(bob, artifact(bob, { work }), { database: racing });
    expect(raced).toBe(true);
    expect(reply.body.result).toMatchObject({ disposition: 'attached', revision: 2 });
    expect(await count('SELECT count(*) AS n FROM submissions WHERE submission_id = ?', reply.body.result!.submissionId)).toBe(1);
  });

  it('refuses a submission beyond the contributor storage cap without partial writes', async () => {
    const erin = await contributor();
    await db.prepare('UPDATE artifact_storage_policy SET contributor_bytes = 100').run();
    try {
      const before = await count('SELECT count(*) AS n FROM versions');
      const reply = await submit(erin, artifact(erin, { work: { ...artifact(erin).work, tmdb: '105' } }));
      expect([reply.status, reply.body]).toEqual([503, { error: { code: 'service_unavailable' } }]);
      expect(await count('SELECT count(*) AS n FROM versions')).toBe(before);
    } finally { await db.prepare('UPDATE artifact_storage_policy SET contributor_bytes = 7000000').run(); }
  });

  it('accepts the cross-language fixture submission signed by its fixed key', async () => {
    const seed = Buffer.from(fixture.authorPrivateKeySeedHex, 'hex');
    const privateKey = await crypto.subtle.importKey('pkcs8', Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), seed]),
      'Ed25519', false, ['sign']);
    const publicKey = Uint8Array.from(atob(fixture.authorPublicKeyBase64), character => character.charCodeAt(0));
    expect(await publicKeyId(publicKey)).toBe(fixture.authorKeyId);
    await db.batch([
      db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('c_7f3a', 'fixture-discord', 'Fixture', 'fixture', 1)`),
      db.prepare("INSERT INTO installations (installation_id, contributor_id, created_at) VALUES ('fixture-installation', 'c_7f3a', 1)"),
      db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
        VALUES (?, 'fixture-installation', 'Ed25519', ?, 'active', 1)`).bind(fixture.authorKeyId, publicKey),
    ]);
    const text = JSON.stringify({ version: 1, submissionId: fixture.artifact.submissionId, artifact: fixture.gzipBase64,
      manifest: Buffer.from(fixture.manifest).toString('base64'), envelope: Buffer.from(fixture.envelope).toString('base64') });
    const response = await handle(await signedPost({ keyId: fixture.authorKeyId, publicKey, privateKey }, '/v1/submissions', text,
      `submit-${fixture.artifact.submissionId}`, undefined, nonce()));
    const reply = await response.json() as { result: Record<string, unknown> };
    expect(response.status).toBe(200);
    expect(reply.result).toMatchObject({ submissionDigest: fixture.artifactDigest, submissionId: fixture.artifact.submissionId,
      outcome: { supports: 2, unassigned: 1 } });
    const stored = await db.prepare(`SELECT e.expression AS entry, f.expression AS filter FROM submission_entries e
      JOIN filter_attestations a USING (submission_id, entry_id) JOIN master_filters f ON f.filter_id = a.filter_id
      WHERE e.submission_id = ? AND e.entry_id = 'e3'`).bind(fixture.artifact.submissionId).first();
    expect(stored).toEqual({ entry: 'Crème brûlée', filter: 'Crème brûlée' });
  });

  it('is routed behind admission and fails closed without SUBMISSION_LIMITS', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ user: { id: alice.discordId } })));
    const env = { APP_ENV: 'local', CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
      TRUSTED_ACTOR_KEY: tunnelActor, DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token',
      MASTER_POLICY: JSON.stringify(policy), SUBMISSION_LIMITS: JSON.stringify(DEFAULT_SUBMISSION_LIMITS) };
    const edge = async () => {
      const value = artifact(alice, { work: { ...artifact(alice).work, tmdb: '106' }, media: { durationMs: 8_160_000,
        matchKeys: [{ type: 'sha256_full', value: hex('9'), sizeBytes: 5 }] } });
      const signed = await signedPost(alice, '/v1/submissions', body(await encode(value, alice)), `submit-${value.submissionId}`,
        Math.floor(Date.now() / 1000), nonce());
      const headers = new Headers(signed.headers);
      headers.set('CF-Connecting-IP', '192.0.2.1');
      return new Request(signed, { headers });
    };
    try {
      expect((await service.fetch(await edge(), env)).status).toBe(200);
      expect((await service.fetch(await edge(), { ...env, SUBMISSION_LIMITS: undefined })).status).toBe(503);
    } finally { vi.unstubAllGlobals(); }
  });
});
