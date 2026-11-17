import type { SqlDatabase } from '../../runtime/contracts';
import { transientStatement } from '../../retention/transient';
import type { Env } from '../../config';
import { z } from 'zod';
import { BodyLimitError, readBoundedBytes, readBoundedJson } from '../../http/bounded-body';
import { discordAuthorizeUrl, discordPage } from './pages';
import { recoveryAuthorization, type RecoveryAuthorization } from '../../identity/recovery';

const startSchema = z.object({
  installationId: z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/),
  keyId: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  publicKey: z.string().regex(/^[A-Za-z0-9+/]{43}=$/),
  confirmationHash: z.string().regex(/^[0-9a-f]{64}$/).optional(),
  operationId: z.string().uuid().optional(),
  oldKeyId: z.string().regex(/^sha256:[0-9a-f]{64}$/).optional(),
}).strict();
const userSchema = z.object({ id: z.string().regex(/^\d{17,20}$/) });
const memberSchema = z.object({ user: userSchema });
const tokenSchema = z.object({ access_token: z.string().regex(/^[\x21-\x7e]{1,2048}$/),
  token_type: z.string().refine((value) => value.toLowerCase() === 'bearer'),
  scope: z.string().max(256).refine((value) =>
    ['identify', 'guilds.members.read'].every((scope) => value.split(' ').includes(scope))) });

const discordOrigin = new URL(discordAuthorizeUrl).origin;
const callbackPath = '/v1/discord/callback';
const cookieName = '__Host-community_oauth';
const ttl = 10 * 60 * 1000;
const headers = { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'" };

function error(status: number, code: string): Response {
  return Response.json({ error: { code } }, { status, headers });
}

function randomToken(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
    byte.toString(16).padStart(2, '0')).join('');
}

async function sha256(value: Uint8Array | string): Promise<string> {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',
    Uint8Array.from(bytes))), (byte) =>
    byte.toString(16).padStart(2, '0')).join('');
}

function browserCookie(request: Request): string | null {
  const cookie = request.headers.get('cookie') ?? '';
  if (cookie.length > 8192) return null;
  const values = cookie.split(';').map((part) => part.trim())
    .filter((part) => part.startsWith(`${cookieName}=`));
  if (values.length !== 1) return null;
  const value = values[0].slice(cookieName.length + 1);
  return /^[0-9a-f]{64}$/.test(value) ? value : null;
}

function configured(request: Request, env: Env): env is Env & {
  CATALOG_DB: SqlDatabase; PUBLIC_ORIGIN: string; DISCORD_CLIENT_ID: string;
  DISCORD_CLIENT_SECRET: string; DISCORD_GUILD_ID: string;
} {
  try {
    const origin = new URL(env.PUBLIC_ORIGIN ?? '');
    return origin.protocol === 'https:' && origin.origin === env.PUBLIC_ORIGIN &&
      request.url.startsWith(`${origin.origin}/`) && !!env.CATALOG_DB &&
      /^\d{17,20}$/.test(env.DISCORD_CLIENT_ID ?? '') &&
      /^\d{17,20}$/.test(env.DISCORD_GUILD_ID ?? '') && !!env.DISCORD_CLIENT_SECRET;
  } catch { return false; }
}

type State = { installation_id: string; key_id: string; public_key: ArrayBuffer;
  confirmation_hash?: string | null };

async function stateFor(request: Request, db: SqlDatabase, stateId: string,
  origin: string, now: number, confirmed: number): Promise<State | null> {
  const browser = browserCookie(request);
  if (!browser || !/^[0-9a-f]{64}$/.test(stateId)) return null;
  return db.prepare(`SELECT * FROM discord_oauth_states
    WHERE state_id = ? AND browser_hash = ? AND expires_at > ?
    AND consumed_at IS NULL AND confirmed = ?`).bind(
    stateId, await sha256(`${origin}:${browser}`), now, confirmed,
  ).first<State>();
}

async function start(request: Request, db: SqlDatabase, origin: string, now: number,
  requireConfirmation: boolean): Promise<Response> {
  const recovery = new URL(request.url).pathname === '/v1/discord/recovery-start';
  let value: unknown;
  if (request.method === 'GET') {
    const url = new URL(request.url);
    if (!(recovery ? [6] : [3, 4]).includes(url.searchParams.size) || url.search.length > 1024 ||
        ['installationId', 'keyId', 'publicKey'].some((key) =>
          url.searchParams.getAll(key).length !== 1) ||
        [...url.searchParams.keys()].some((key) => url.searchParams.getAll(key).length !== 1)) return error(400, 'invalid_start');
    value = { installationId: url.searchParams.get('installationId')!,
      keyId: url.searchParams.get('keyId')!, publicKey: url.searchParams.get('publicKey')!,
      ...(url.searchParams.has('confirmationHash') ?
        { confirmationHash: url.searchParams.get('confirmationHash')! } : {}),
      ...(recovery ? { operationId: url.searchParams.get('operationId'), oldKeyId: url.searchParams.get('oldKeyId') } : {}) };
  } else {
    if (request.headers.get('origin') !== origin ||
        request.headers.get('content-type') !== 'application/json' ||
        request.headers.has('content-encoding')) return error(403, 'invalid_start');
    try { value = await readBoundedJson(request, 2048); }
    catch (failure) { return error(failure instanceof BodyLimitError ? 413 : 400, 'invalid_start'); }
  }
  const parsed = startSchema.safeParse(value);
  if (!parsed.success) return error(400, 'invalid_start');
  const input = parsed.data;
  if (recovery ? !input.operationId || !input.oldKeyId || !input.confirmationHash : input.operationId || input.oldKeyId) {
    return error(400, 'invalid_start');
  }
  if (requireConfirmation && !input.confirmationHash) return error(400, 'invalid_start');
  let publicKey: Uint8Array;
  try { publicKey = Uint8Array.from(atob(input.publicKey!), (char) => char.charCodeAt(0)); }
  catch { return error(400, 'invalid_start'); }
  if (publicKey.length !== 32 || `sha256:${await sha256(publicKey)}` !== input.keyId ||
      btoa(String.fromCharCode(...publicKey)) !== input.publicKey) return error(400, 'invalid_start');
  const stateId = randomToken();
  const browser = randomToken();
  const statement = input.confirmationHash ? db.prepare(`INSERT INTO discord_oauth_states
    (state_id, browser_hash, installation_id, key_id, public_key, expires_at, confirmation_hash)
    VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(stateId, await sha256(`${origin}:${browser}`),
    input.installationId, input.keyId, publicKey, now + ttl, input.confirmationHash) : db.prepare(`INSERT INTO discord_oauth_states
    (state_id, browser_hash, installation_id, key_id, public_key, expires_at)
    VALUES (?, ?, ?, ?, ?, ?)`).bind(stateId, await sha256(`${origin}:${browser}`),
    input.installationId, input.keyId, publicKey, now + ttl);
  if (recovery) {
    const owner = await db.prepare(`SELECT installation.contributor_id FROM installations AS installation
      JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
      JOIN installation_keys AS key ON key.installation_id = installation.installation_id
      WHERE installation.installation_id = ? AND key.key_id = ? AND contributor.state = 'active'
        AND NOT EXISTS (SELECT 1 FROM installation_keys WHERE installation_id = ? AND state = 'active' AND key_id != ?)`)
      .bind(input.installationId, input.oldKeyId!, input.installationId, input.oldKeyId!).first<{ contributor_id: string }>();
    if (!owner || input.oldKeyId === input.keyId) return error(409, 'recovery_required');
    const intent: RecoveryAuthorization = { version: 1, operationId: input.operationId!, installationId: input.installationId,
      oldKeyId: input.oldKeyId!, newKeyId: input.keyId, publicKey: input.publicKey,
      confirmationHash: input.confirmationHash!, expiresAt: now + ttl };
    await db.batch([statement, db.prepare(`INSERT INTO operations
      (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
      VALUES (?, 'key_recovery_authorization', ?, ?, 'completed', ?, ?, ?)`)
      .bind(owner.contributor_id, stateId, await sha256(JSON.stringify(intent)), JSON.stringify(intent), now, now)]);
  } else await statement.run();
  const responseHeaders = { ...headers,
    'Set-Cookie': `${cookieName}=${browser}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=600` };
  const confirmationUrl = `${origin}/v1/discord/confirm?state=${stateId}`;
  return request.method === 'GET' ? new Response(null, { status: 303,
    headers: { ...responseHeaders, Location: confirmationUrl } }) :
    Response.json({ confirmationUrl, stateId, expiresAt: now + ttl },
      { headers: responseHeaders });
}

async function confirm(request: Request, env: Env & { CATALOG_DB: SqlDatabase;
  PUBLIC_ORIGIN: string; DISCORD_CLIENT_ID: string }, now: number): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'POST') return error(404, 'not_found');
  if (request.method === 'POST') {
    if (request.headers.get('origin') !== env.PUBLIC_ORIGIN) return error(403, 'invalid_confirmation');
    try { await readBoundedBytes(request, 0); }
    catch { return error(403, 'invalid_confirmation'); }
  }
  const url = new URL(request.url);
  const stateId = url.searchParams.get('state') ?? '';
  if (url.searchParams.size !== 1) return error(400, 'invalid_state');
  const state = await stateFor(request, env.CATALOG_DB, stateId, env.PUBLIC_ORIGIN, now, 0);
  if (!state) {
    return error(400, 'invalid_state');
  }
  if (request.method === 'GET') {
    const recovery = await recoveryAuthorization(env.CATALOG_DB, stateId);
    // The same-origin form POST needs its Origin header for the confirmation check.
    return discordPage({ stateId, installationId: state.installation_id, keyId: state.key_id,
      recovery: Boolean(recovery) }, { ...headers, 'Referrer-Policy': 'same-origin' });
  }
  const browser = browserCookie(request)!;
  const claimed = await env.CATALOG_DB.prepare(`UPDATE discord_oauth_states SET confirmed = 1
    WHERE state_id = ? AND browser_hash = ? AND confirmed = 0
      AND consumed_at IS NULL AND expires_at > ?`).bind(
    stateId, await sha256(`${env.PUBLIC_ORIGIN}:${browser}`), now,
  ).run();
  if (claimed.meta.changes !== 1) return error(400, 'invalid_state');
  const target = new URL(discordAuthorizeUrl);
  target.search = new URLSearchParams({ client_id: env.DISCORD_CLIENT_ID,
    redirect_uri: `${env.PUBLIC_ORIGIN}${callbackPath}`, response_type: 'code',
    scope: 'identify guilds.members.read', state: stateId }).toString();
  return new Response(null, { status: 303, headers: { ...headers, Location: target.toString() } });
}

async function callback(request: Request, env: Env & { CATALOG_DB: SqlDatabase;
  PUBLIC_ORIGIN: string; DISCORD_CLIENT_ID: string; DISCORD_CLIENT_SECRET: string;
  DISCORD_GUILD_ID: string }, now: number, fetchProvider: typeof fetch): Promise<Response> {
  const url = new URL(request.url);
  const stateId = url.searchParams.get('state') ?? '';
  const denied = url.searchParams.has('error');
  const code = url.searchParams.get('code') ?? '';
  const permitted = denied ? ['state', 'iss', 'error', 'error_description', 'error_uri'] : ['state', 'iss', 'code'];
  if (url.search.length > 2048 || url.searchParams.getAll('state').length !== 1 ||
      (url.searchParams.has('iss') && url.searchParams.get('iss') !== discordOrigin) ||
      [...url.searchParams.keys()].some((name) => !permitted.includes(name) ||
        url.searchParams.getAll(name).length !== 1) ||
      (denied ? !/^[A-Za-z0-9_]{1,64}$/.test(url.searchParams.get('error') ?? '') :
        !/^[\x21-\x7e]{1,256}$/.test(code))) {
    return error(400, 'invalid_callback');
  }
  const state = await stateFor(request, env.CATALOG_DB, stateId,
    env.PUBLIC_ORIGIN, now, 1);
  if (!state) return error(400, 'invalid_state');
  const claimed = await env.CATALOG_DB.prepare(`UPDATE discord_oauth_states SET consumed_at = ?
    WHERE state_id = ? AND browser_hash = ? AND confirmed = 1
      AND consumed_at IS NULL AND expires_at > ?
      AND installation_id = ? AND key_id = ? AND public_key = ?`).bind(
    now, stateId, await sha256(`${env.PUBLIC_ORIGIN}:${browserCookie(request)!}`), now,
    state.installation_id, state.key_id, state.public_key,
  ).run();
  if (claimed.meta.changes !== 1) return error(400, 'invalid_state');
  if (denied) return error(403, 'discord_denied');
  try {
    const tokenResponse = await fetchProvider('https://discord.com/api/v10/oauth2/token', {
      method: 'POST', redirect: 'error', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ client_id: env.DISCORD_CLIENT_ID,
        client_secret: env.DISCORD_CLIENT_SECRET, grant_type: 'authorization_code', code,
        redirect_uri: `${env.PUBLIC_ORIGIN}${callbackPath}` }),
      signal: AbortSignal.timeout(5000),
    });
    if (!tokenResponse.ok) return error(503, 'discord_unavailable');
    const token = tokenSchema.parse(await readBoundedJson(tokenResponse, 16_384));
    const providerHeaders = { Authorization: `Bearer ${token.access_token}` };
    const userResponse = await fetchProvider('https://discord.com/api/v10/users/@me', {
      headers: providerHeaders, redirect: 'error', signal: AbortSignal.timeout(5000),
    });
    if (!userResponse.ok) return error(503, 'discord_unavailable');
    const user = userSchema.parse(await readBoundedJson(userResponse, 16_384));
    const memberResponse = await fetchProvider(
      `https://discord.com/api/v10/users/@me/guilds/${env.DISCORD_GUILD_ID}/member`, {
        headers: providerHeaders, redirect: 'error', signal: AbortSignal.timeout(5000),
      });
    if (memberResponse.status === 404) return error(403, 'not_a_member');
    if (!memberResponse.ok) return error(503, 'discord_unavailable');
    const member = memberSchema.parse(await readBoundedJson(memberResponse, 16_384));
    if (member.user.id !== user.id) return error(503, 'discord_unavailable');
    const recovery = await recoveryAuthorization(env.CATALOG_DB, stateId);
    if (recovery) {
      const owner = await env.CATALOG_DB.prepare(`SELECT 1 FROM contributors
        WHERE contributor_id = ? AND discord_user_id = ? AND state = 'active'`)
        .bind(recovery.contributorId, user.id).first();
      if (!owner || recovery.intent.expiresAt <= now) return error(403, 'recovery_ownership_required');
    }
    await (state.confirmation_hash ? env.CATALOG_DB.prepare(`INSERT INTO enrollment_states
      (state_id, installation_id, key_id, public_key, provider_subject, expires_at, confirmation_hash)
      VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(stateId, state.installation_id,
      state.key_id, state.public_key, user.id, now + ttl, state.confirmation_hash) : env.CATALOG_DB.prepare(`INSERT INTO enrollment_states
      (state_id, installation_id, key_id, public_key, provider_subject, expires_at)
      VALUES (?, ?, ?, ?, ?, ?)`).bind(stateId, state.installation_id,
      state.key_id, state.public_key, user.id, now + ttl)).run();
    return discordPage(null, { ...headers,
      'Set-Cookie': `${cookieName}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0` }, stateId);
  } catch { return error(503, 'discord_unavailable'); }
}

export type OAuthAdmission = (request: Request) => Promise<'allowed' | 'unavailable'>;

export async function discordOAuth(request: Request, env: Env, now = Date.now(),
  fetchProvider: typeof fetch = fetch, admit?: OAuthAdmission,
  requireConfirmation = false): Promise<Response> {
  if (!configured(request, env) || !Number.isSafeInteger(now) || now < 0) {
    return error(503, 'service_unavailable');
  }
  const url = new URL(request.url);
  try {
    if (!['/v1/discord/start', '/v1/discord/recovery-start', '/v1/discord/confirm', callbackPath].includes(url.pathname)) {
      return error(404, 'not_found');
    }
    const admission = admit ? await admit(request) : 'unavailable';
    if (admission !== 'allowed') return error(503, 'service_unavailable');
    if (['/v1/discord/start', '/v1/discord/recovery-start'].includes(url.pathname) &&
      (request.method === 'GET' || request.method === 'POST') &&
      (request.method === 'GET' || !url.search)) {
      return await start(request, env.CATALOG_DB, env.PUBLIC_ORIGIN, now, requireConfirmation);
    }
    if (url.pathname === '/v1/discord/confirm') return await confirm(request, env, now);
    if (url.pathname === callbackPath && request.method === 'GET') {
      return await callback(request, env, now, fetchProvider);
    }
    return error(404, 'not_found');
  } catch { return error(503, 'service_unavailable'); }
}

export async function pruneDiscordOAuth(db: SqlDatabase, now: number, limit: number): Promise<number> {
  if (!Number.isSafeInteger(now) || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) {
    throw new Error('invalid_limit');
  }
  const result = await transientStatement(db, 'oauthStates', now, limit, 'prune').run();
  return result.meta.changes;
}
