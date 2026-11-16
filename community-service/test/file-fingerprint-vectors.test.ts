import { expect, it } from 'vitest';
import fixture from '../../tests/fixtures/community/file-fingerprints-v1.json';

const sha256 = async (bytes: Uint8Array) => Array.from(new Uint8Array(
  await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
  (byte) => byte.toString(16).padStart(2, '0')).join('');

it.each(fixture.vectors)('independently verifies fingerprint bytes for $size_bytes bytes', async (vector) => {
  const size = BigInt(vector.size_bytes);
  const width = size < 1048576n ? size : 1048576n;
  const ranges: [bigint, bigint][] = [];
  if (size) for (let index = 0n; index < 9n; index++) {
    const offset = (size - width) * index / 8n;
    const previous = ranges.at(-1);
    if (previous && offset <= previous[0] + previous[1]) {
      previous[1] = offset + width - previous[0];
    } else ranges.push([offset, width]);
  }
  expect(ranges.map(([offset, length]) => [Number(offset), Number(length)]))
    .toEqual(vector.ranges);
  const content = Uint8Array.from({ length: vector.size_bytes }, (_, index) => index % 256);
  const domain = new TextEncoder().encode(fixture.algorithm + '\0');
  const payload = new Uint8Array(domain.length + 12 + ranges.length * 16 +
    ranges.reduce((sum, [, length]) => sum + Number(length), 0));
  payload.set(domain);
  const view = new DataView(payload.buffer);
  view.setBigUint64(domain.length, size, false);
  view.setUint32(domain.length + 8, ranges.length, false);
  let position = domain.length + 12;
  for (const [offset, length] of ranges) {
    view.setBigUint64(position, offset, false);
    view.setBigUint64(position + 8, length, false);
    position += 16;
    payload.set(content.subarray(Number(offset), Number(offset + length)), position);
    position += Number(length);
  }
  expect(await sha256(payload)).toBe(vector.sampled_sha256);
  expect(await sha256(content)).toBe(vector.full_sha256);
});