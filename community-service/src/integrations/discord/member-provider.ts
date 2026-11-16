import { readBoundedJson } from '../../http/bounded-body';

export type MemberResult = {
  state: 'eligible' | 'ineligible' | 'unavailable';
  retryAfter: number;
};

function seconds(value: string | number | null): number | null {
  if (value === null || value === '') return null;
  if (typeof value === 'string' && !/^\d+(?:\.\d+)?$/.test(value)) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 2_147_483_647 ? Math.ceil(number) : null;
}

/** Fetch one member; only Discord's explicit Unknown Member error proves absence. */
export async function fetchBotMember(guildId: string, userId: string, token: string,
  client: typeof fetch = fetch): Promise<MemberResult> {
  const unavailable: MemberResult = { state: 'unavailable', retryAfter: 5 };
  try {
    if (![guildId, userId].every((id) => /^\d{17,20}$/.test(id)) ||
        !/^[\x21-\x7e]{1,2048}$/.test(token)) return unavailable;
    const response = await client(`https://discord.com/api/v10/guilds/${guildId}/members/${userId}`, {
      headers: { Authorization: `Bot ${token}` }, redirect: 'error', signal: AbortSignal.timeout(5000),
    });
    const reset = seconds(response.headers.get('X-RateLimiter-Reset-After'));
    const exhausted = response.headers.get('X-RateLimiter-Remaining') === '0';
    const retry = seconds(response.headers.get('Retry-After'));
    const headerDelay = Math.max(retry ?? 0, exhausted || response.status === 429 ? reset ?? 0 : 0);
    const failed: MemberResult = { state: 'unavailable',
      retryAfter: Math.max(headerDelay, response.status === 429 || response.status === 401 ||
        response.status === 403 || exhausted ? 60 : 5) };
    let value: unknown;
    try { value = await readBoundedJson(response, 16_384); }
    catch { return failed; }
    const body = value && typeof value === 'object' ? value as Record<string, unknown> : {};
    const bodyRetry = typeof body.retry_after === 'number' ? seconds(body.retry_after) : null;
    if (response.status === 429) return { state: 'unavailable',
      retryAfter: Math.max(1, retry ?? 0, bodyRetry ?? 0, reset ?? 0, retry === null && bodyRetry === null ? 60 : 0) };
    const retryAfter = Math.max(headerDelay, exhausted ? Math.max(1, reset ?? 60) : 0);
    if (response.status === 404 && body.code === 10007) return { state: 'ineligible', retryAfter };
    if (!response.ok) return { state: 'unavailable',
      retryAfter: Math.max(retryAfter, response.status === 401 || response.status === 403 ? 60 : 5) };
    const user = body.user;
    if (!user || typeof user !== 'object' || (user as Record<string, unknown>).id !== userId) {
      return { state: 'unavailable', retryAfter: Math.max(5, retryAfter) };
    }
    return { state: 'eligible', retryAfter };
  } catch { return unavailable; }
}