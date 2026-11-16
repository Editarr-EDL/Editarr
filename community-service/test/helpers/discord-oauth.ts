import { env } from './environment';
import { expect, vi } from 'vitest';
import identityMigration from '../../migrations/0001_identity_operations.sql?raw';
import budgetMigration from '../../migrations/0002_budget_ledger.sql?raw';
import quotaMigration from '../../migrations/0003_quotas.sql?raw';
import admissionMigration from '../../migrations/0004_request_admission.sql?raw';
import oauthMigration from '../../migrations/0005_discord_oauth.sql?raw';
import { discordOAuth as broker } from '../../src/integrations/discord/oauth';

export const db = env.CATALOG_DB;
export const origin = 'https://community.editarr.org';
export const now = Date.parse('2026-09-30T12:00:00Z');
export const guildId = '1554889314549243986';
export const userId = '1554889314549243999';
export const config = { ...env, PUBLIC_ORIGIN: origin, DISCORD_CLIENT_ID: '1554924079486607490',
  DISCORD_CLIENT_SECRET: 'synthetic-secret', DISCORD_GUILD_ID: guildId };
export const key = Uint8Array.from({ length: 32 }, (_, index) => index);
export const hex = (bytes: Uint8Array) => Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
export const json = (value: unknown, status = 200) => Response.json(value, { status });
export const discordOAuth: typeof broker = (input, bindings, at, client) =>
  broker(input, bindings, at, client, async () => 'allowed');

export async function initializeOAuthDb() {
  for (const migration of [identityMigration, budgetMigration, quotaMigration, admissionMigration]) {
    await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  }
  await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at)
    VALUES ('pre-oauth', 'pre-oauth-user', 'Preserved', 1)`).run();
  await db.exec(oauthMigration.replace(/\s*\n\s*/g, ' '));
}

export async function start(at = now, host = origin, publicKey = key) {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(publicKey));
  const installationId = crypto.randomUUID();
  const keyId = `sha256:${hex(new Uint8Array(digest))}`;
  const params = new URLSearchParams({ installationId, keyId,
    publicKey: btoa(String.fromCharCode(...publicKey)) });
  const startUrl = `${host}/v1/discord/start?${params}`;
  const response = await discordOAuth(new Request(startUrl), { ...config, PUBLIC_ORIGIN: host }, at);
  expect(response.status).toBe(303);
  const cookie = response.headers.get('set-cookie')!.split(';')[0];
  const confirmationUrl = response.headers.get('location')!;
  const stateId = new URL(confirmationUrl).searchParams.get('state')!;
  return { cookie, confirmationUrl, stateId, installationId, keyId, startUrl };
}

export function request(url: string, cookie: string, method = 'GET', extra: HeadersInit = {}) {
  return new Request(url, { method, headers: { Cookie: cookie, ...extra } });
}

export async function confirm(flow: Awaited<ReturnType<typeof start>>, at = now, host = origin) {
  return discordOAuth(request(flow.confirmationUrl, flow.cookie, 'POST', { Origin: host }),
    { ...config, PUBLIC_ORIGIN: host }, at);
}

export function provider(result: 'member' | 'nonmember' | 'outage' | 'wrong-user' | 'bad-scope') {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (result === 'outage') throw new Error('provider failure with sensitive detail');
    if (url.endsWith('/oauth2/token')) return json({ access_token: 'synthetic-access-token',
      token_type: 'Bearer', scope: result === 'bad-scope' ? 'identify' :
        'identify guilds.members.read' });
    if (url.endsWith('/users/@me')) return json({ id: userId });
    if (url.endsWith(`/users/@me/guilds/${guildId}/member`)) return result === 'nonmember' ?
      json({ message: 'Unknown Member' }, 404) : json({ user: {
        id: result === 'wrong-user' ? '1554889314549243988' : userId,
      } });
    throw new Error('unexpected provider endpoint');
  }) as typeof fetch;
}