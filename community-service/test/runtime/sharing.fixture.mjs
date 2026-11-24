import assert from 'node:assert/strict';
import { journey, expectStatus } from './journey.fixture.mjs';
import { artifact, body, encode, support } from '../helpers/submissions.ts';
import { receiptSigningBytes, snapshotSigningBytes, verifyServiceSignature } from '../../src/master/signing.ts';

// Master-set sharing over the real HTTP server: submit, download, vote, report and sync.
export async function exercise(database, runtime) {
  await journey(database, async ({ enroll, post, read, admin, env }) => {
    const author = await enroll('1554889314549243999'), reader = await enroll('1554889314549243988');
    const value = artifact(author, { entries: [support('e1', 600_000, 605_000), support('e2', 1_200_000, 1_203_000)] });
    const encoded = await encode(value, author);
    const operation = `submit-${value.submissionId}`;
    const [first, retry] = await Promise.all([post(author, '/v1/submissions', body(encoded), operation),
      post(author, '/v1/submissions', body(encoded), operation)]);
    assert.deepEqual(expectStatus(first), expectStatus(retry));
    const submitted = expectStatus(first).result;
    assert.equal(submitted.disposition, 'created_confirmed');
    assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM submissions').first('n'), 1);
    const matched = expectStatus(await post(reader, '/v1/match', { version: 1, keys: [{ type: 'tmdb', kind: 'movie', id: '603' }],
      label: null, runtimeMs: 8_160_000 })).results;
    assert.equal(matched[0].versionId, submitted.versionId);
    assert.equal(matched[0].filterCount, 2);
    const master = expectStatus(await read(reader, `/v1/versions/${submitted.versionId}/master?knownRevision=0`));
    const keys = JSON.parse(env.DOWNLOAD_RECEIPT_KEYS);
    const { signature, ...unsigned } = master.snapshotSignature;
    assert.ok(await verifyServiceSignature(keys, unsigned.serviceKeyId, snapshotSigningBytes(unsigned), signature));
    assert.ok(await verifyServiceSignature(keys, unsigned.serviceKeyId, receiptSigningBytes(master.receipt.receipt), master.receipt.signature));
    const snapshot = JSON.parse(Buffer.from(master.snapshot, 'base64').toString());
    assert.equal(snapshot.filters.length, 2);
    assert.deepEqual(expectStatus(await read(reader, `/v1/versions/${submitted.versionId}/master?knownRevision=1`)),
      { version: 1, changed: false, versionId: submitted.versionId, revision: 1 });
    const filterId = snapshot.filters[0].filterId, receiptId = master.receipt.receipt.receiptId;
    const voteOperation = `vote-${crypto.randomUUID()}`;
    const voted = expectStatus(await post(reader, `/v1/filters/${filterId}/votes`, { version: 1, vote: 'up', receiptId }, voteOperation));
    assert.equal(voted.result.revision, 2);
    assert.deepEqual(expectStatus(await post(reader, `/v1/filters/${filterId}/votes`, { version: 1, vote: 'up', receiptId }, voteOperation)), voted);
    assert.equal(expectStatus(await post(author, `/v1/filters/${filterId}/votes`, { version: 1, vote: 'up', receiptId },
      `vote-${crypto.randomUUID()}`), 403).error.reason,
      'receipt_required');
    assert.deepEqual(expectStatus(await read(reader, `/v1/versions/${submitted.versionId}/votes`)).votes, [{ filterId, vote: 'up' }]);
    expectStatus(await post(reader, '/v1/reports', { version: 1, target: { type: 'filter', id: filterId }, reason: 'inaccurate' }));
    const changed = expectStatus(await post(reader, '/v1/sync/revisions', { version: 1, runId: 'journey-run',
      items: [{ versionId: submitted.versionId, revision: 1 }] }));
    assert.deepEqual(changed.changed, [{ versionId: submitted.versionId, status: 'changed', revision: 2 }]);
    assert.equal(expectStatus(await post(reader, '/v1/sync/revisions', { version: 1, runId: 'another-run',
      items: [{ versionId: submitted.versionId, revision: 2 }] }), 429).error.code, 'sync_rate_limited');
    for (const command of ['quarantine', 'restore']) {
      const inspected = expectStatus(await admin(`/admin/v1/moderation/version/${submitted.versionId}`));
      expectStatus(await admin('/admin/v1/moderation', { version: 1, actionId: crypto.randomUUID(), command, resource: 'version',
        targetId: submitted.versionId, expectedRevision: inspected.revision, reason: 'Synthetic master moderation' }));
      if (command === 'quarantine') assert.equal(expectStatus(await read(reader, `/v1/versions/${submitted.versionId}/master`), 410)
        .error.code, 'version_unavailable');
    }
    assert.equal(expectStatus(await read(reader, `/v1/versions/${submitted.versionId}/master?knownRevision=2`)).changed, false);
  }, runtime);
}
