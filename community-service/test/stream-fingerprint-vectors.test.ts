import { expect, it } from 'vitest';
import fixture from '../../tests/fixtures/community/stream-fingerprints-v1.json';

it.each(fixture.vectors)('independently verifies $channel packet framing', async (vector) => {
  const chunks: Uint8Array[] = [];
  const count = (value: number) => {
    const bytes = new Uint8Array(4);
    new DataView(bytes.buffer).setUint32(0, value, false);
    chunks.push(bytes);
  };
  const hex = (value: string) => Uint8Array.from(value.match(/../g)!, pair => parseInt(pair, 16));
  const frame = (bytes: Uint8Array) => { count(bytes.length); chunks.push(bytes); };
  chunks.push(new TextEncoder().encode(vector.algorithm + '\0'));
  frame(new TextEncoder().encode(vector.codec));
  frame(hex(fixture.configuration_hex));
  count(fixture.samples_hex.length);
  for (const sample of fixture.samples_hex) {
    count(sample.length);
    for (const packet of sample) frame(hex(packet));
  }
  const payload = new Uint8Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { payload.set(chunk, offset); offset += chunk.length; }
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', payload)), byte => byte.toString(16).padStart(2, '0')).join('');
  expect(digest).toBe(vector.digest);
});