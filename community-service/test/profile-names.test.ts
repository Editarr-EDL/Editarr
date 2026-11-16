import { describe, expect, it } from 'vitest';
import { generatedName, validatedName } from '../src/identity/profile';

describe('contributor display labels', () => {
  it('generates bounded three-word labels', () => {
    for (let index = 0; index < 20; index += 1) {
      const name = generatedName();
      expect(name.split(' ')).toHaveLength(3);
      expect(validatedName(name)).toBe(name);
    }
  });

  it('normalizes Unicode and accepts markup only as plain display text', () => {
    expect(validatedName('  Cafe\u0301  ')).toBe('Caf\u00e9');
    expect(validatedName('<b>Name</b>')).toBe('<b>Name</b>');
  });

  it.each(['ab', 'x'.repeat(41), 'A\nB', 'A\u202eB', 'A\u0000B', 'A\ud800B', null])
    ('rejects invalid name %j', (name) => {
      expect(() => validatedName(name)).toThrow('invalid_display_name');
    });
});