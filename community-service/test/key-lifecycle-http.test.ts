import { env } from './helpers/environment';
import { beforeAll, expect, it, vi } from 'vitest';
import { contributorKeys } from '../src/http/key-lifecycle';
import { contributorIdentity } from '../src/http/identity';
import { authenticateContributor } from '../src/security/contributor-auth';
import { renameContributor } from '../src/identity/rename';
import service from '../src/index';
import { now, origin, prepareKeyDatabase, preparedRotation, registerKey, signedPost, signedRead } from './helpers/key-lifecycle';

const db = env.CATALOG_DB;
const eligible = async () => 'eligible' as const;
beforeAll(() => prepareKeyDatabase(db));
async function handle(request: Request) {
  return contributorKeys(request, db, 'community.example', now, eligible);
}

function edge(request: Request) {
  const headers = new Headers(request.headers);
  headers.set('CF-Connecting-IP', '192.0.2.10');
  return new Request(request, { headers, });
}

it('reconciles prepared/not-committed and lost committed response using only the new private key', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  const statusRequest = () => signedPost(plan.replacement, '/v1/keys/rotation-status', plan.body, plan.operationId);
  const absent = await handle(await statusRequest());
  expect(absent.status).toBe(200);
  expect(await absent.json()).toEqual({ version: 1, state: 'not_committed', result: null });
  expect(await db.prepare('SELECT 1 FROM operations WHERE contributor_id = ?').bind(old.contributorId).first()).toBeNull();
  const response = await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  expect(response.status).toBe(200);
  const committed = await handle(await statusRequest());
  expect(committed.status).toBe(200);
  expect(await committed.json()).toMatchObject({ state: 'completed', result: {
    contributorId: old.contributorId, installationId: old.installationId,
    oldKeyId: old.keyId, newKeyId: plan.replacement.keyId, operationId: plan.operationId } });
  const retryOld = await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  expect(retryOld.status).toBe(401);
  expect(await retryOld.json()).toEqual({ error: { code: 'key_inactive' } });
  const operation = await handle(await signedRead(plan.replacement, `/v1/key-operations/key_rotation/${plan.operationId}`));
  expect(operation.status).toBe(200);
  expect(await operation.text()).not.toContain(old.discordId);
});

it('retirement denies later reads and mutations on separate handlers without affecting siblings', async () => {
  const old = await registerKey(db);
  const sibling = await registerKey(db, old.contributorId);
  const plan = await preparedRotation(old);
  expect((await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId))).status).toBe(200);
  expect((await contributorIdentity(await signedRead(old, '/v1/identity'), db, 'community.example', now, eligible, 'test')).status).toBe(401);
  for (const key of [plan.replacement, sibling]) {
    expect((await contributorIdentity(await signedRead(key, '/v1/identity'), db, 'community.example', now, eligible, 'test')).status).toBe(200);
  }
  const mutation = await signedPost(old, '/v1/identity', JSON.stringify({ displayName: 'Changed' }), crypto.randomUUID());
  await expect(authenticateContributor(mutation, db, 'community.example', now, eligible)).rejects.toMatchObject({ code: 'key_inactive' });
  for (const path of ['/v1/catalog', '/v1/artifacts/example', '/v1/key-operations/key_rotation/example']) {
    await expect(authenticateContributor(await signedRead(old, path), db, 'community.example', now, eligible))
      .rejects.toMatchObject({ code: 'key_inactive' });
  }
});

it('rejects a previously authenticated mutation after rotation commits', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  const stale = await authenticateContributor(await signedPost(old, '/v1/identity',
    JSON.stringify({ displayName: 'Stale Change' }), crypto.randomUUID()), db, 'community.example', now, eligible);
  await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  await expect(renameContributor(db, stale, 'Stale Change', now * 1000)).rejects.toThrow('key_inactive');
  expect(await db.prepare("SELECT 1 FROM operations WHERE contributor_id = ? AND kind = 'profile_name'")
    .bind(old.contributorId).first()).toBeNull();
});

it('preserves committed rotation despite post-commit accounting failure', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  await db.exec("CREATE TRIGGER fail_key_telemetry BEFORE INSERT ON usage_observations BEGIN SELECT RAISE(ABORT, 'fixture_telemetry_failure'); END;");
  try {
    const response = await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
    expect(response.status).toBe(200);
    expect(response.headers.get('Community-Usage-Status')).toBe('unavailable');
    const recovered = await handle(await signedPost(plan.replacement, '/v1/keys/rotation-status', plan.body, plan.operationId));
    expect(recovered.status).toBe(200);
    expect(await recovered.json()).toMatchObject({ state: 'completed' });
  } finally { await db.exec('DROP TRIGGER fail_key_telemetry;'); }
});

it('marks compromised retired history revoked without retiring the current replacement', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  const response = await handle(await signedPost(plan.replacement, '/v1/keys/revoke',
    JSON.stringify({ version: 1, installationId: old.installationId, keyId: old.keyId }), crypto.randomUUID()));
  expect(response.status).toBe(200);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'revoked' });
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(plan.replacement.keyId).first()).toEqual({ state: 'active' });
  expect((await handle(await signedPost(plan.replacement, '/v1/keys/rotation-status', plan.body, plan.operationId))).status).toBe(200);
});

it('rejects substituted new proof, changed operation/service and foreign installation without writes', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  const foreign = await registerKey(db);
  const changed = JSON.parse(plan.body);
  for (const body of [JSON.stringify({ ...changed, newProof: changed.oldProof }),
    JSON.stringify({ ...changed, installationId: foreign.installationId }),
    JSON.stringify({ ...changed, unexpected: 'field' })]) {
    expect((await handle(await signedPost(old, '/v1/keys/rotate', body, plan.operationId))).status).toBeGreaterThanOrEqual(400);
  }
  expect((await handle(await signedPost(old, '/v1/keys/rotate', plan.body, 'different-operation'))).status).toBe(401);
  expect((await handle(await signedPost(foreign, '/v1/keys/rotate', plan.body, plan.operationId))).status).toBe(401);
  const stolen = await handle(await signedPost(foreign, '/v1/keys/rotation-status', plan.body, plan.operationId));
  expect(stolen.status).toBe(401);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'active' });
});

it('rejects cross-contributor operation lookup, including public guessed IDs', async () => {
  const old = await registerKey(db);
  const foreign = await registerKey(db);
  const plan = await preparedRotation(old);
  await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  const response = await handle(await signedRead(foreign, `/v1/key-operations/key_rotation/${plan.operationId}`));
  expect(response.status).toBe(404);
  expect(await response.text()).not.toContain(old.contributorId);
  expect((await handle(new Request(`${origin}/v1/key-operations/key_rotation/${plan.operationId}`))).status).toBe(401);
});

it('rejects valid dual proofs that attempt to reuse another contributor key', async () => {
  const old = await registerKey(db);
  const foreign = await registerKey(db);
  const plan = await preparedRotation(old, crypto.randomUUID(), foreign);
  const response = await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  expect(response.status).toBe(409);
  for (const key of [old, foreign]) {
    expect(await db.prepare('SELECT state, installation_id FROM installation_keys WHERE key_id = ?')
      .bind(key.keyId).first()).toEqual({ state: 'active', installation_id: key.installationId });
  }
  expect(await db.prepare('SELECT 1 FROM operations WHERE contributor_id = ?').bind(old.contributorId).first()).toBeNull();
});

it('simultaneous signed rotations leave only one completed operation and replacement', async () => {
  const old = await registerKey(db);
  const plans = await Promise.all([preparedRotation(old), preparedRotation(old)]);
  const requests = await Promise.all(plans.map((plan) => signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId)));
  const responses = await Promise.all(requests.map(handle));
  expect(responses.filter((response) => response.status === 200)).toHaveLength(1);
  expect((await db.prepare("SELECT key_id FROM installation_keys WHERE installation_id = ? AND state = 'active'")
    .bind(old.installationId).all()).results).toHaveLength(1);
  expect((await db.prepare("SELECT operation_id FROM operations WHERE contributor_id = ? AND kind = 'key_rotation'")
    .bind(old.contributorId).all()).results).toHaveLength(1);
});

it('revokes a target immediately and reconciles from another still-active owned installation', async () => {
  const target = await registerKey(db);
  const sibling = await registerKey(db, target.contributorId);
  const operationId = crypto.randomUUID();
  const body = JSON.stringify({ version: 1, installationId: target.installationId, keyId: target.keyId });
  expect((await handle(await signedPost(target, '/v1/keys/revoke', body, operationId))).status).toBe(200);
  expect((await handle(await signedRead(target, `/v1/key-operations/key_revocation/${operationId}`))).status).toBe(401);
  expect((await handle(await signedRead(sibling, `/v1/key-operations/key_revocation/${operationId}`))).status).toBe(200);
  expect((await handle(await signedPost(sibling, '/v1/keys/revoke', body, operationId))).status).toBe(200);
  const foreign = await registerKey(db);
  const attack = await handle(await signedPost(foreign, '/v1/keys/revoke',
    JSON.stringify({ version: 1, installationId: sibling.installationId, keyId: sibling.keyId }), crypto.randomUUID()));
  expect(attack.status).toBe(409);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(sibling.keyId).first()).toEqual({ state: 'active' });
});

it('fails closed for suspended contributors, expired signatures and unavailable membership', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  for (const membership of [async () => 'unavailable' as const, async () => 'ineligible' as const]) {
    const response = await contributorKeys(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId),
      db, 'community.example', now, membership);
    expect([403, 503]).toContain(response.status);
  }
  expect((await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId, now - 1000))).status).toBe(401);
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = ?").bind(old.contributorId).run();
  expect((await handle(await signedPost(plan.replacement, '/v1/keys/rotation-status', plan.body, plan.operationId))).status).toBe(401);
});

it('bounds bodies and rejects malformed payloads, unexpected encoding and wrong service', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  for (const path of ['/v1/keys/rotate', '/v1/keys/rotation-status']) {
    const key = path.endsWith('rotation-status') ? plan.replacement : old;
    expect((await handle(await signedPost(key, path, '{', plan.operationId))).status).toBe(400);
    expect((await handle(await signedPost(key, path, ' '.repeat(2049), plan.operationId))).status).toBe(413);
    const encoded = await signedPost(key, path, plan.body, plan.operationId);
    encoded.headers.set('content-encoding', 'gzip');
    expect((await handle(encoded)).status).toBe(400);
    const wrongService = await contributorKeys(await signedPost(key, path, plan.body, plan.operationId),
      db, 'other.example', now, eligible);
    expect(wrongService.status).toBe(400);
  }
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'active' });
});

it('does not resurrect a later retired or revoked replacement during reconciliation', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  await handle(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId));
  await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = ?")
    .bind(now * 1000, plan.replacement.keyId).run();
  expect((await handle(await signedPost(plan.replacement, '/v1/keys/rotation-status', plan.body, plan.operationId))).status).toBe(401);
});

it('wires Node service admission/membership but never enables nonlocal rotation', async () => {
  const old = await registerKey(db);
  const plan = await preparedRotation(old);
  const timestamp = Math.floor(Date.now() / 1000);
  const provider = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ user: { id: old.discordId } }));
  const bindings = { ...env, PUBLIC_ORIGIN: origin, POLICY_VERSION: 'fixture',
    DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-rotation-bot-token'};
  try {
    const blocked = await service.fetch(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId, timestamp),
      { ...bindings, APP_ENV: 'production' });
    expect(blocked.status).toBe(503);
    expect(provider).not.toHaveBeenCalled();
    const denied = await service.fetch(edge(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId, timestamp)),
      { ...bindings, TRUSTED_ACTOR_KEY: () => null });
    expect(denied.status).toBe(503);
    expect(provider).not.toHaveBeenCalled();
    const response = await service.fetch(edge(await signedPost(old, '/v1/keys/rotate', plan.body, plan.operationId, timestamp)), bindings);
    expect(response.status).toBe(200);
    expect(provider).toHaveBeenCalledOnce();
    expect(await response.text()).not.toContain(bindings.DISCORD_BOT_TOKEN);
  } finally { provider.mockRestore(); }
});