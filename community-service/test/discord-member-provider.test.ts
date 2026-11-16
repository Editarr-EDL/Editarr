import { describe, expect, it, vi } from 'vitest';
import { fetchBotMember } from '../src/integrations/discord/member-provider';

const guild = '1554889314549243986';
const user = '1554889314549243999';
const token = 'synthetic-bot-secret';

describe('single-member bot REST adapter', () => {
  it.each(['0x0', ' ', '-1', 'Infinity', '1e2'])('rejects malformed retry header %j', async (retry) => {
    expect(await fetchBotMember(guild, user, token, async () => Response.json({}, {
      status: 429, headers: { 'Retry-After': retry },
    }))).toEqual({ state: 'unavailable', retryAfter: 60 });
  });

  it.each([401, 403, 429])('preserves conservative fallback with malformed status %s body', async (status) => {
    expect(await fetchBotMember(guild, user, token, async () => new Response('invalid', { status })))
      .toEqual({ state: 'unavailable', retryAfter: 60 });
  });

  it('preserves exhausted bucket fallback with malformed JSON and no reset', async () => {
    expect(await fetchBotMember(guild, user, token, async () => new Response('invalid', {
      headers: { 'X-RateLimiter-Remaining': '0' },
    }))).toEqual({ state: 'unavailable', retryAfter: 60 });
  });
  it('uses only the fixed single-member endpoint and accepts pending membership without roles', async () => {
    const client = vi.fn(async () => Response.json({ user: { id: user }, pending: true, roles: [] }));
    expect(await fetchBotMember(guild, user, token, client)).toEqual({ state: 'eligible', retryAfter: 0 });
    expect(client).toHaveBeenCalledWith(`https://discord.com/api/v10/guilds/${guild}/members/${user}`,
      expect.objectContaining({ headers: { Authorization: `Bot ${token}` }, redirect: 'error',
        signal: expect.any(AbortSignal) }));
  });

  it.each([[404, 10007, 'ineligible'], [404, 10004, 'unavailable'], [404, 0, 'unavailable'],
    [403, 50001, 'unavailable'], [401, 0, 'unavailable'], [503, 0, 'unavailable']])(
    'classifies status %s / Discord code %s as %s', async (status, code, state) => {
      const result = await fetchBotMember(guild, user, token,
        async () => Response.json({ code, message: 'sensitive detail' }, { status: Number(status) }));
      expect(result.state).toBe(state);
      expect(JSON.stringify(result)).not.toContain('sensitive');
    },
  );

  it('honors fractional reset and retry values without shortening a provider cooldown', async () => {
    expect(await fetchBotMember(guild, user, token, async () => Response.json({ retry_after: 130.1 }, {
      status: 429, headers: { 'Retry-After': '120', 'X-RateLimiter-Reset-After': '140.2' },
    }))).toEqual({ state: 'unavailable', retryAfter: 141 });
    expect(await fetchBotMember(guild, user, token, async () => Response.json({ user: { id: user } }, {
      headers: { 'X-RateLimiter-Remaining': '0', 'X-RateLimiter-Reset-After': '2.1' },
    }))).toEqual({ state: 'eligible', retryAfter: 3 });
  });

  it.each([null, { user: { id: 1554889314549243999 } }, { user: { id: guild } }, {}])(
    'rejects malformed or substituted member %j', async (body) => {
      expect((await fetchBotMember(guild, user, token, async () => Response.json(body))).state)
        .toBe('unavailable');
    },
  );

  it.each(['timeout', 'redirect', 'oversized', 'bad_json'])('fails closed on %s', async (mode) => {
    const client: typeof fetch = async () => {
      if (mode === 'timeout') throw new Error('sensitive detail');
      if (mode === 'redirect') return new Response(null, { status: 302,
        headers: { Location: 'https://attacker.example' } });
      return new Response(mode === 'oversized' ? ' '.repeat(16_385) : 'invalid');
    };
    expect((await fetchBotMember(guild, user, token, client)).state).toBe('unavailable');
  });

  it.each([429, 200])('retains cooldown headers despite invalid JSON on status %s', async (status) => {
    expect(await fetchBotMember(guild, user, token, async () => new Response('invalid', {
      status, headers: { 'Retry-After': '120', 'X-RateLimiter-Remaining': '0',
        'X-RateLimiter-Reset-After': '130' },
    }))).toEqual({ state: 'unavailable', retryAfter: 130 });
    expect(await fetchBotMember(guild, user, token, async () => Response.json({}, {
      status, headers: { 'Retry-After': '120', 'X-RateLimiter-Remaining': '0',
        'X-RateLimiter-Reset-After': '130' },
    }))).toEqual({ state: 'unavailable', retryAfter: 130 });
  });
});