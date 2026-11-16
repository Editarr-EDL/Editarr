import { env } from './helpers/environment';
import { beforeAll, expect, it, vi } from 'vitest';
import { confirmationHash } from '../src/identity/confirmation';
import { contributorRecovery } from '../src/http/recovery';
import { contributorEnrollment } from '../src/http/enrollment';
import { contributorIdentity } from '../src/http/identity';
import { discordService } from '../src/integrations/discord/service';
import { authenticateContributor } from '../src/security/contributor-auth';
import { renameContributor } from '../src/identity/rename';
import service from '../src/index';
import { now, origin, prepareKeyDatabase, registerKey, signingKey, signedPost, signedRead } from './helpers/key-lifecycle';
import contract from '../../tests/fixtures/community/key-recovery-v1.json';

const db = env.CATALOG_DB;
const timestamp = now * 1000;
const secret = 'c'.repeat(64);
const config = { ...env, PUBLIC_ORIGIN: origin, POLICY_VERSION: 'fixture',
  DISCORD_CLIENT_ID: '1554924079486607490', DISCORD_CLIENT_SECRET: 'synthetic-secret',
  DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-bot'};
const eligible = async () => 'eligible' as const;
beforeAll(() => prepareKeyDatabase(db));

function edge(request: Request) {
  const headers = new Headers(request.headers);
  headers.set('CF-Connecting-IP', '192.0.2.33');
  return new Request(request, { headers, });
}

function provider(subject: string, mode = 'member') {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (mode === 'outage') throw new Error('synthetic provider unavailable');
    if (url.endsWith('/oauth2/token')) return Response.json({ access_token: 'synthetic-access',
      token_type: 'Bearer', scope: 'identify guilds.members.read' });
    if (url.endsWith('/users/@me')) return Response.json({ id: subject });
    if (mode === 'nonmember') return Response.json({ message: 'Not a member' }, { status: 404 });
    return Response.json({ user: { id: subject } });
  }) as typeof fetch;
}

async function flow(old: Awaited<ReturnType<typeof registerKey>>, recover = true, subject = old.discordId) {
  const candidate = await signingKey();
  const operationId = crypto.randomUUID();
  const installationId = recover ? old.installationId : crypto.randomUUID();
  const parameters = new URLSearchParams({ installationId, keyId: candidate.keyId,
    publicKey: btoa(String.fromCharCode(...candidate.publicKey)), confirmationHash: await confirmationHash(secret),
    ...(recover ? { operationId, oldKeyId: old.keyId } : {}) });
  const started = await discordService(edge(new Request(`${origin}/v1/discord/${recover ? 'recovery-start' : 'start'}?${parameters}`)), config, timestamp);
  expect(started.status).toBe(303);
  const cookie = started.headers.get('set-cookie')!.split(';')[0];
  const confirmationUrl = started.headers.get('location')!;
  const stateId = new URL(confirmationUrl).searchParams.get('state')!;
  const page = await discordService(edge(new Request(confirmationUrl, { headers: { cookie } })), config, timestamp);
  if (recover) expect(await page.text()).toContain('old copies will lose access immediately');
  expect((await discordService(edge(new Request(confirmationUrl, { method: 'POST', headers: { cookie, origin } })), config, timestamp)).status).toBe(303);
  const callback = await discordService(edge(new Request(`${origin}/v1/discord/callback?state=${stateId}&code=synthetic-code`,
    { headers: { cookie } })), config, timestamp, provider(subject));
  const body = JSON.stringify({ version: 1, stateId, installationId, confirmationSecret: secret,
    ...(recover ? { operationId, oldKeyId: old.keyId } : {}) });
  return { candidate, operationId: recover ? operationId : stateId, stateId, body, callback, installationId };
}

it('requires the owning fresh Discord account, not a known name or enrollment URL', async () => {
  const old = await registerKey(db);
  const foreign = await registerKey(db);
  const plan = await flow(old, true, foreign.discordId);
  expect(plan.callback.status).toBe(403);
  expect(await plan.callback.json()).toEqual({ error: { code: 'recovery_ownership_required' } });
  expect(await db.prepare('SELECT 1 FROM enrollment_states WHERE state_id = ?').bind(plan.stateId).first()).toBeNull();
  expect((await contributorRecovery(edge(await signedPost(plan.candidate, '/v1/keys/recover', plan.body, plan.operationId)),
    config, timestamp, provider(old.discordId))).status).toBe(409);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'active' });
});

it('full fresh OAuth recovery preserves contributor identity and rejects old reads and stale mutations immediately', async () => {
  const old = await registerKey(db);
  const sibling = await registerKey(db, old.contributorId);
  const stale = await authenticateContributor(await signedPost(old, '/v1/identity',
    JSON.stringify({ displayName: 'Stale Name' }), crypto.randomUUID()), db, 'community.example', now, eligible);
  const historicalBytes = new TextEncoder().encode('historical artifact');
  const historicalSignature = await crypto.subtle.sign('Ed25519', old.privateKey, historicalBytes);
  const plan = await flow(old);
  expect(plan.callback.status).toBe(200);
  async function edgeRequest(current: typeof plan, path: string): Promise<Request> {
    return edge(await signedPost(current.candidate, path, current.body, current.operationId));
  }
  const response = await contributorRecovery(await edgeRequest(plan, '/v1/keys/recover'), config, timestamp, provider(old.discordId));
  expect(response.status).toBe(200);
  const result = await response.json();
  expect(Object.keys(result as object).sort()).toEqual([...contract.envelopeFields].sort());
  expect(Object.keys((result as { result: object }).result).sort()).toEqual([...contract.resultFields].sort());
  expect(result).toMatchObject({ state: 'completed', result: { contributorId: old.contributorId,
    installationId: old.installationId, oldKeyId: old.keyId, newKeyId: plan.candidate.keyId } });
  expect(JSON.stringify(result)).not.toContain(old.discordId);
  expect(JSON.stringify(result)).not.toContain(secret);
  expect((await contributorIdentity(await signedRead(old, '/v1/identity'), db, 'community.example', now, eligible, 'fixture')).status).toBe(401);
  expect((await contributorIdentity(await signedRead(sibling, '/v1/identity'), db, 'community.example', now, eligible, 'fixture')).status).toBe(200);
  await expect(renameContributor(db, stale, 'Stale Name', timestamp)).rejects.toThrow('key_inactive');
  const oldRow = await db.prepare('SELECT public_key FROM installation_keys WHERE key_id = ?').bind(old.keyId).first<{ public_key: ArrayBuffer }>();
  const historicalKey = await crypto.subtle.importKey('raw', new Uint8Array(oldRow!.public_key), 'Ed25519', false, ['verify']);
  expect(await crypto.subtle.verify('Ed25519', historicalKey, historicalSignature, historicalBytes)).toBe(true);
  const status = await contributorRecovery(await edgeRequest(plan, '/v1/keys/recovery-status'), config, timestamp, provider(old.discordId));
  expect(status.status).toBe(200);
  expect(await status.json()).toEqual(result);
  const retry = await contributorRecovery(await edgeRequest(plan, '/v1/keys/recover'), config, timestamp, provider(old.discordId));
  expect(await retry.json()).toEqual(result);
  expect((await db.prepare("SELECT * FROM quota_charges WHERE contributor_id = ? AND charge_id LIKE 'key_recovery:%'")
    .bind(old.contributorId).all()).results).toHaveLength(0);
});

it('a reinstall registers another installation without replacing the original or resetting contributor limits', async () => {
  const old = await registerKey(db);
  const plan = await flow(old, false);
  expect(plan.callback.status).toBe(200);
  const response = await contributorEnrollment(edge(await signedPost(plan.candidate, '/v1/enrollments/complete',
    plan.body, plan.operationId)), config, timestamp, provider(old.discordId));
  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ contributorId: old.contributorId, installationId: plan.installationId, keyId: plan.candidate.keyId });
  expect((await db.prepare("SELECT installation_id FROM installations WHERE contributor_id = ?").bind(old.contributorId).all()).results).toHaveLength(2);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'active' });
});

it('two authorized concurrent candidates cannot retire a newly recovered key', async () => {
  const old = await registerKey(db);
  const plans = await Promise.all([flow(old), flow(old)]);
  expect((await contributorRecovery(edge(await signedPost(plans[0].candidate, '/v1/keys/recovery-status', plans[0].body,
    plans[0].operationId)), config, timestamp, provider(old.discordId))).status).toBe(200);
  const responses = await Promise.all(plans.map(async (plan) => contributorRecovery(edge(await signedPost(plan.candidate,
    '/v1/keys/recover', plan.body, plan.operationId)), config, timestamp, provider(old.discordId))));
  expect(responses.map((response) => response.status).sort()).toEqual([200, 409]);
  expect((await db.prepare("SELECT key_id FROM installation_keys WHERE installation_id = ? AND state = 'active'")
    .bind(old.installationId).all()).results).toHaveLength(1);
});

it('rejects stolen candidate proof, provider outage and missing admission without mutation', async () => {
  const old = await registerKey(db);
  const plan = await flow(old);
  const wrong = await signingKey();
  const stolen = await contributorRecovery(edge(await signedPost(wrong, '/v1/keys/recover', plan.body, plan.operationId)), config, timestamp, provider(old.discordId));
  expect(stolen.status).toBe(401);
  const unavailable = await contributorRecovery(edge(await signedPost(plan.candidate, '/v1/keys/recover', plan.body, plan.operationId)), config, timestamp, provider(old.discordId, 'outage'));
  expect(unavailable.status).toBe(503);
  const missingEdge = await contributorRecovery(await signedPost(plan.candidate, '/v1/keys/recover', plan.body, plan.operationId), config, timestamp, provider(old.discordId));
  expect(missingEdge.status).toBe(503);
  expect(await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?').bind(old.keyId).first()).toEqual({ state: 'active' });
});

it('Node service retains its nonlocal guard and routes local recovery through native admission', async () => {
  const old = await registerKey(db);
  const plan = await flow(old);
  const at = Math.floor(Date.now() / 1000);
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ user: { id: old.discordId } }));
  try {
    expect((await service.fetch(edge(await signedPost(plan.candidate, '/v1/keys/recover', plan.body, plan.operationId, at)),
      { ...config, APP_ENV: 'production' })).status).toBe(503);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect((await service.fetch(edge(await signedPost(plan.candidate, '/v1/keys/recover', plan.body, plan.operationId, at)),
      { ...config, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect(fetchSpy).not.toHaveBeenCalled();
  } finally { fetchSpy.mockRestore(); }
});