import { describe, expect, it, vi } from 'vitest';
import { createOAuthAdmission } from '../src/integrations/discord/admission';
import { tunnelActor } from '../src/runtime/environment.mjs';

function edgeRequest(address: string): Request {
  return new Request('https://community.editarr.org/v1/discord/start', {
    headers: { 'CF-Connecting-IP': address },
  });
}

describe('trusted edge OAuth admission', () => {
  it('admits repeated trusted requests without usage buckets', async () => {
    const admission = createOAuthAdmission({ trustedActorKey: tunnelActor, policy: async () => 'allowed' });
    for (let index = 0; index < 400; index += 1) {
      expect(await admission(edgeRequest('192.0.2.1'))).toBe('allowed');
    }
  });

  it('rejects tunnel subrequest IP substitution', () => {
    const input = edgeRequest('192.0.2.1');
    input.headers.set('CF-Worker', 'attacker.example');
    expect(tunnelActor(input)).toBeNull();
  });
  it('fails closed without an explicitly trusted tunnel resolver and ignores forwarding aliases', async () => {
    const policy = vi.fn(async (): Promise<'allowed'> => 'allowed');
    const admission = createOAuthAdmission({ policy });
    expect(await admission(edgeRequest('192.0.2.1'))).toBe('unavailable');
    expect(policy).not.toHaveBeenCalled();
    expect(tunnelActor(new Request('https://community.editarr.org', {
      headers: { 'X-Forwarded-For': '192.0.2.1' },
    }))).toBeNull();
  });

  it('normalizes equivalent IPv6 addresses and validates IPv4', () => {
    expect(tunnelActor(edgeRequest('2001:db8:0:0:0:0:0:1')))
      .toBe(tunnelActor(edgeRequest('2001:db8::1')));
    expect(tunnelActor(edgeRequest('192.0.2.1'))).toBe('cloudflare-ip:192.0.2.1');
  });

  it.each(['attacker.example', '999.0.2.1', '192.0.2.1,192.0.2.2', '::invalid', '', 'user@192.0.2.1'])(
    'rejects invalid edge address %s', (address) => {
      expect(tunnelActor(edgeRequest(address))).toBeNull();
    },
  );

  it('redacts service admission failures', async () => {
    const admission = createOAuthAdmission({ trustedActorKey: tunnelActor,
      policy: async () => { throw new Error('sensitive'); } });
    expect(await admission(edgeRequest('192.0.2.1'))).toBe('unavailable');
  });
});