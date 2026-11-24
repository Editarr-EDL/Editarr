import assert from 'node:assert/strict';
import { journey, expectStatus, patch, timestamp, origin } from './journey.fixture.mjs';
import { preparedRotation, signingKey, signedPost, signedRead } from '../helpers/key-lifecycle.ts';

async function protectedRequests(key, time = timestamp()) {
  const revision = '10000000-0000-4000-8000-000000000001';
  const reads = ['/v1/identity', '/v1/receipt-keys', '/v1/aliases', '/v1/versions/v_journey/master?knownRevision=1',
    '/v1/versions/v_journey/votes',
    ...['key_rotation', 'key_revocation', 'key_recovery'].map(kind => `/v1/key-operations/${kind}/${revision}`)];
  const writes = ['/v1/match', '/v1/submissions', '/v1/filters/f_journey/votes', '/v1/sync/revisions', '/v1/reports',
    '/v1/keys/rotate', '/v1/keys/revoke'];
  return [...await Promise.all(reads.map(path => signedRead(key, path, time))),
    ...await Promise.all(writes.map(path => signedPost(key, path, '{}', crypto.randomUUID(), time))),
    await patch(key, { displayName: 'Auth Matrix Name' }, undefined, undefined, time)];
}

export async function exercise(database, runtime) {
  await journey(database, async ({ enroll, read, send, post, authorize, provider, env }) => {
    const alice = await enroll('1554889314549243999');
    const bob = await enroll('1554889314549243988');
    const sibling = await enroll(alice.subject);
    const operationsBefore = await database.prepare('SELECT COUNT(*) AS n FROM operations').first('n');
    const noncesBefore = await database.prepare('SELECT COUNT(*) AS n FROM request_nonces').first('n');
    for (const request of await protectedRequests(alice)) {
      const unsigned = request.clone();
      unsigned.headers.delete('signature'); unsigned.headers.delete('signature-input');
      assert.equal(expectStatus(await send(unsigned), 401).error.code, 'invalid_signature', request.url);
    }
    for (const offset of [-300, 300]) {
      for (const request of await protectedRequests(alice, timestamp() + offset)) {
        const response = await send(request);
        assert.equal(expectStatus(response, 401).error.code, 'clock_skew', request.url);
        assert.match(response.headers['cache-control'], /no-store/);
      }
    }
    const signed = await signedPost(alice, '/v1/keys/revoke', '{}', crypto.randomUUID(), timestamp());
    for (const [name, value] of [['content-type', 'application/octet-stream'], ['content-encoding', 'gzip'],
      ['idempotency-key', 'altered-operation'], ['content-digest', 'sha-256=:invalid:']]) {
      const headers = new Headers(signed.headers); headers.set(name, value);
      expectStatus(await send(new Request(signed.url, { method: 'POST', headers, body: '{}' })),
        ['content-type', 'content-encoding'].includes(name) ? 400 : 401);
    }
    expectStatus(await send(new Request(origin + '/v1/identity?changed=1', { headers: (await signedRead(alice, '/v1/identity', timestamp())).headers })), 401);
    expectStatus(await send(new Request('https://operator.example.test/v1/identity', {
      headers: (await signedRead(alice, '/v1/identity', timestamp())).headers })), 503);
    expectStatus(await send(new Request('https://untrusted.example/v1/identity', { headers: signed.headers })), 400);
    for (const name of ['signature', 'signature-input', 'host', 'cf-connecting-ip']) {
      const request = await signedRead(alice, '/v1/identity', timestamp());
      expectStatus(await send(request, [name, request.headers.get(name) ?? (name === 'host' ? 'community.example' : '192.0.2.1')]), 400);
    }
    for (const ip of ['', 'not-an-ip']) {
      const request = await signedPost(alice, '/v1/keys/revoke', '{}', crypto.randomUUID(), timestamp());
      request.headers.set('cf-connecting-ip', ip);
      expectStatus(await send(request), 503);
    }
    const worker = await signedPost(alice, '/v1/keys/revoke', '{}', crypto.randomUUID(), timestamp());
    worker.headers.set('cf-worker', 'untrusted.example'); expectStatus(await send(worker), 503);
    for (const path of ['/admin/', '/admin/v1/session', '/admin/v1/reports',
      '/admin/v1/dashboard/overview', '/admin/v1/moderation']) {
      const request = path.endsWith('/moderation') ? await signedPost(alice, path, '{}', crypto.randomUUID(), timestamp()) :
        await signedRead(alice, path, timestamp());
      const headers = new Headers(request.headers);
      headers.set('cf-access-authenticated-user-email', 'operator@example.test');
      headers.set('x-community-operator', '1'); headers.set('origin', env.OPERATOR_ORIGIN);
      expectStatus(await send(new Request(env.OPERATOR_ORIGIN + path, { method: request.method, headers,
        ...(request.method === 'POST' ? { body: '{}' } : {}) })), 401);
    }
    const replayedRead = await signedRead(alice, '/v1/identity', timestamp());
    expectStatus(await send(replayedRead.clone())); expectStatus(await send(replayedRead.clone()));
    assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces').first('n'), noncesBefore);
    assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM operations').first('n'), operationsBefore);
    for (const offset of [-300, 300]) {
      const response = await send(await signedRead(alice, '/v1/identity', timestamp() + offset));
      assert.equal(expectStatus(response, 401).error.code, 'clock_skew');
      assert.equal(response.headers['cache-control'], 'no-store');
      const candidate = await signingKey(), enrollment = await authorize(candidate, alice.subject);
      assert.equal(expectStatus(await send(await signedPost(candidate, '/v1/enrollments/complete',
        JSON.stringify(enrollment.body), enrollment.operation, timestamp() + offset)), 401).error.code, 'clock_skew');
      const rotation = await preparedRotation(alice);
      assert.equal(expectStatus(await send(await signedPost(rotation.replacement, '/v1/keys/rotation-status',
        rotation.body, rotation.operationId, timestamp() + offset)), 401).error.code, 'clock_skew');
      const recovery = await authorize(candidate, alice.subject, alice);
      for (const path of ['/v1/keys/recover', '/v1/keys/recovery-status']) {
        assert.equal(expectStatus(await send(await signedPost(candidate, path, JSON.stringify(recovery.body),
          recovery.operation, timestamp() + offset)), 401).error.code, 'clock_skew');
      }
    }
    assert.notEqual(alice.contributorId, bob.contributorId);
    assert.equal(alice.contributorId, sibling.contributorId);
    assert.notEqual(alice.installationId, sibling.installationId);
    const operation = crypto.randomUUID();
    const rename = await patch(alice, { displayName: 'Cedar Frost Grove' }, operation);
    expectStatus(await send(rename.clone()));
    expectStatus(await send(rename.clone()));
    assert.equal(expectStatus(await read(sibling, '/v1/identity')).displayName, 'Cedar Frost Grove');
    expectStatus(await send(await patch(alice, { displayName: 'Birch Frost Grove' }, operation)), 409);
    const rotation = await preparedRotation(alice);
    const beforeRetirement = await protectedRequests(alice);
    const result = expectStatus(await post(alice, '/v1/keys/rotate', rotation.body, rotation.operationId));
    for (const request of [...beforeRetirement, ...await protectedRequests(alice)]) {
      assert.equal(expectStatus(await send(request), 401).error.code, 'key_inactive', request.url);
    }
    expectStatus(await read(alice, '/v1/identity'), 401);
    expectStatus(await read(sibling, '/v1/identity'));
    assert.deepEqual(expectStatus(await post(rotation.replacement, '/v1/keys/rotation-status', rotation.body, rotation.operationId)), result);
    expectStatus(await read(bob, `/v1/key-operations/key_rotation/${rotation.operationId}`), 404);
    const active = { ...rotation.replacement, installationId: alice.installationId };
    const candidate = await signingKey(), recovery = await authorize(candidate, alice.subject, active);
    const recovered = expectStatus(await post(candidate, '/v1/keys/recover', recovery.body, recovery.operation));
    assert.equal(recovered.result.contributorId, alice.contributorId);
    assert.deepEqual(expectStatus(await post(candidate, '/v1/keys/recovery-status', recovery.body, recovery.operation)), recovered);
    assert.deepEqual(expectStatus(await post(candidate, '/v1/keys/recover', recovery.body, recovery.operation)), recovered);
    expectStatus(await read(active, '/v1/identity'), 401);
    expectStatus(await read(sibling, '/v1/identity'));
    // A restored old copy has the same retired key and cannot silently enroll or rotate.
    expectStatus(await read({ ...alice }, '/v1/identity'), 401);
    await database.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp() - 901).run();
    provider.mode = 'outage'; expectStatus(await read(candidate, '/v1/identity'), 503);
    // Advance persisted provider cooldown without sleeping or weakening production limits.
    await database.prepare('UPDATE discord_membership_refresh SET retry_at = 0').run();
    provider.mode = 'nonmember'; expectStatus(await read(candidate, '/v1/identity'), 403);
    await database.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp() - 901).run();
    provider.mode = 'member'; expectStatus(await read(candidate, '/v1/identity'));
    const revoke = { version: 1, installationId: sibling.installationId, keyId: sibling.keyId };
    const beforeRevocation = await protectedRequests(sibling);
    expectStatus(await post(candidate, '/v1/keys/revoke', revoke));
    for (const request of [...beforeRevocation, ...await protectedRequests(sibling)]) {
      assert.equal(expectStatus(await send(request), 401).error.code, 'key_inactive', request.url);
    }
    expectStatus(await read(sibling, '/v1/identity'), 401);
    expectStatus(await read(bob, '/v1/identity'));
    const tampered = await signedPost(candidate, '/v1/keys/revoke', JSON.stringify(revoke), crypto.randomUUID(), timestamp());
    expectStatus(await send(new Request(tampered.url, { method: 'POST', headers: tampered.headers, body: '{}' })), 401);
    const counts = await database.prepare('SELECT COUNT(*) AS count FROM contributors').first('count');
    assert.equal(counts, 2);
  }, runtime);
}
