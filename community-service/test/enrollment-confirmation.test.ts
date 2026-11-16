import { describe, expect, it } from 'vitest';
import { confirmationHash } from '../src/identity/confirmation';

describe('backend-held enrollment confirmation', () => {
  it('binds exact canonical secrets without returning them', async () => {
    const secret = 'a'.repeat(64);
    const hash = await confirmationHash(secret);
    expect(hash).toMatch(/^[0-9a-f]{64}$/);
    expect(hash).not.toBe(secret);
    expect(await confirmationHash(secret)).toBe(hash);
    expect(await confirmationHash('b'.repeat(64))).not.toBe(hash);
  });

  it.each(['', 'A'.repeat(64), 'a'.repeat(63), 'a'.repeat(65), 'g'.repeat(64)])
    ('rejects noncanonical confirmation %j', async (secret) => {
      await expect(confirmationHash(secret)).rejects.toThrow('invalid_confirmation');
    });
});