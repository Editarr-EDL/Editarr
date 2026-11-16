import { describe, expect, it } from 'vitest';

import { RequestSignatureError, RequestSignatureTimeError, verifyRequestSignature } from '../src/security/request-signatures';
import fixtures from './fixtures/request-signatures-v1.json';

const decode = (value: string) => Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
const publicKey = decode(fixtures.public_key_base64);
const policy = { now: 1700000030, maxLifetimeSeconds: 120, maxClockSkewSeconds: 5,
  expectedAuthority: 'community.example', expectedKeyId: 'fixture-key' };

function signedRequest(vector: typeof fixtures.vectors[number]) {
  const headers = new Headers();
  for (const [name, value] of Object.entries(vector.headers)) {
    if (value !== undefined) headers.set(name, value);
  }
  return { method: vector.method, url: vector.url, headers,
    body: decode(vector.body_base64) };
}

describe('community signed-request profile v1', () => {
  it('distinguishes verified clock failures from tampering outside the time window', async () => {
    for (const now of [1699999990, 1700000070]) {
      const request = signedRequest(fixtures.vectors[1]);
      await expect(verifyRequestSignature(request, publicKey, { ...policy, now }))
        .rejects.toThrow(RequestSignatureTimeError);
      request.body[0] ^= 1;
      await expect(verifyRequestSignature(request, publicKey, { ...policy, now }))
        .rejects.not.toThrow(RequestSignatureTimeError);
    }
    await expect(verifyRequestSignature(signedRequest(fixtures.vectors[1]), publicKey,
      { ...policy, maxLifetimeSeconds: 30 })).rejects.not.toThrow(RequestSignatureTimeError);
  });

  it.each(fixtures.vectors)('verifies independent Python-signed $name bytes', async (vector) => {
    const key = await crypto.subtle.importKey('raw', publicKey as BufferSource,
      'Ed25519', false, ['verify']);
    const signature = decode(vector.headers.signature.split(':')[1]);
    expect(await crypto.subtle.verify('Ed25519', key, signature as BufferSource,
      new TextEncoder().encode(vector.signature_base))).toBe(true);
    const verified = await verifyRequestSignature(signedRequest(vector), publicKey, policy);
    expect(verified).toMatchObject({ keyId: 'fixture-key',
      nonce: 'AQIDBAUGBwgJCgsMDQ4PEA', operationId: vector.headers['idempotency-key'] ?? null,
      contentDigest: vector.headers['content-digest'] });
  });

  it('rejects changed request bytes, target, authority, and trusted key selection', async () => {
    const vector = fixtures.vectors[2];
    const changes = [
      (request: ReturnType<typeof signedRequest>) => { request.method = 'PUT'; },
      (request: ReturnType<typeof signedRequest>) => { request.url += '&extra=yes'; },
      (request: ReturnType<typeof signedRequest>) => { request.url += '%ZZ'; },
      (request: ReturnType<typeof signedRequest>) => { request.url += 'é'; },
      (request: ReturnType<typeof signedRequest>) => { request.url = request.url.replace('contributions', 'other'); },
      (request: ReturnType<typeof signedRequest>) => { request.body[0] ^= 1; },
      (request: ReturnType<typeof signedRequest>) => { request.headers.set('content-type', 'application/octet-stream'); },
      (request: ReturnType<typeof signedRequest>) => { request.headers.delete('content-encoding'); },
      (request: ReturnType<typeof signedRequest>) => { request.headers.set('idempotency-key', 'other'); },
      (request: ReturnType<typeof signedRequest>) => { request.headers.delete('idempotency-key'); },
      (request: ReturnType<typeof signedRequest>) => { request.headers.set('content-digest', fixtures.vectors[0].headers['content-digest']); },
    ];
    for (const change of changes) {
      const request = signedRequest(vector);
      change(request);
      await expect(verifyRequestSignature(request, publicKey, policy)).rejects.toThrow(RequestSignatureError);
    }
    await expect(verifyRequestSignature(signedRequest(vector), publicKey,
      { ...policy, expectedAuthority: 'other.example' })).rejects.toThrow(RequestSignatureError);
    await expect(verifyRequestSignature(signedRequest(vector), publicKey,
      { ...policy, expectedKeyId: 'other-key' })).rejects.toThrow(RequestSignatureError);
    await expect(verifyRequestSignature(signedRequest(vector), new Uint8Array(32), policy))
      .rejects.toThrow(RequestSignatureError);
  });

  it('rejects ambiguous or malformed signature fields and stale requests', async () => {
    const vector = fixtures.vectors[1];
    for (const [name, value] of [
      ['signature-input', vector.headers['signature-input'] + ', ' + vector.headers['signature-input']],
      ['signature', vector.headers['signature'] + ', ' + vector.headers['signature']],
      ['signature-input', vector.headers['signature-input'].replace(';alg=', ';nonce="extra";alg=')],
      ['signature-input', vector.headers['signature-input'].replace(';alg=', ';alg="ed25519";alg=')],
      ['signature-input', vector.headers['signature-input'].replace(';created=', ';expires=1700000060;created=')],
      ['signature-input', vector.headers['signature-input'].replace('"@query" ', '')],
      ['signature-input', vector.headers['signature-input'].replace('ed25519', 'rsa-pss-sha512')],
      ['signature-input', vector.headers['signature-input'].replace('1700000060', '1700000001')],
      ['signature-input', vector.headers['signature-input'].replace('AQIDBAUGBwgJCgsMDQ4PEA', '_'.repeat(22))],
      ['signature', vector.headers['signature'].replace(/.$/, '')],
    ]) {
      const request = signedRequest(vector);
      request.headers.set(name, value);
      await expect(verifyRequestSignature(request, publicKey, policy)).rejects.toThrow(RequestSignatureError);
    }
    for (const now of [1699999990, 1700000070]) {
      await expect(verifyRequestSignature(signedRequest(vector), publicKey, { ...policy, now }))
        .rejects.toThrow(RequestSignatureError);
    }
    for (const [name, value] of [
      ['signature', vector.headers.signature], ['signature-input', vector.headers['signature-input']],
    ]) {
      const request = signedRequest(vector);
      request.headers.append(name, value);
      await expect(verifyRequestSignature(request, publicKey, policy)).rejects.toThrow(RequestSignatureError);
    }
    await expect(verifyRequestSignature(signedRequest(vector), publicKey,
      { ...policy, maxLifetimeSeconds: 30 })).rejects.toThrow(RequestSignatureError);
    for (const now of [1699999995, 1700000065]) {
      await expect(verifyRequestSignature(signedRequest(vector), publicKey, { ...policy, now }))
        .resolves.toMatchObject({ keyId: 'fixture-key' });
    }
  });
});