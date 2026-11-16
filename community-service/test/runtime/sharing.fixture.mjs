import assert from 'node:assert/strict';
import { existsSync, statSync } from 'node:fs';
import { journey, expectStatus, timestamp } from './journey.fixture.mjs';
import { preparedRotation, signedPost, signedRead } from '../helpers/key-lifecycle.ts';
import { fixtureKey, syntheticPublication, uploadBody, base64 } from '../helpers/publication-signatures.ts';
import { verifiedCase } from '../helpers/catalog.ts';
import { matchEvidenceSchema } from '../../src/catalog/compatibility-inputs.ts';
import { verifyReceipt } from '../../src/contributions/receipts.ts';
import { previewRetention } from '../../src/retention/policy.ts';

export async function exercise(database, runtime) {
  await journey(database, async ({ enroll, post, read, send, admin, env, provider, runtime: current }) => {
    const author = await fixtureKey();
    await database.batch([
      database.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
        VALUES ('contributor-1', '1554889314549243977', 'Public Name', 'public name', 1)`),
      database.prepare("INSERT INTO installations VALUES ('installation-1', 'contributor-1', 1)"),
      database.prepare("INSERT INTO installation_keys VALUES ('key-1', 'installation-1', 'Ed25519', ?, 'active', 1, NULL)").bind(author.publicKey),
    ]);
    const reader = await enroll('1554889314549243999'), sibling = await enroll(reader.subject);
    const stranger = await enroll('1554889314549243988');
    const first = await syntheticPublication(), second = await syntheticPublication();
    const failedOperation = crypto.randomUUID();
    const storageBefore = await database.prepare('SELECT * FROM artifact_storage_usage ORDER BY scope_id').all();
    database.connection.exec(`CREATE TRIGGER synthetic_publication_failure AFTER INSERT ON artifact_payloads
      BEGIN SELECT RAISE(ABORT, 'synthetic_disk_failure'); END`);
    try {
      expectStatus(await post(author, '/v1/contributions', uploadBody(first), failedOperation), 503);
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM contribution_revisions').first('n'), 0);
      assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM operations WHERE operation_id = ?').bind(failedOperation).first('n'), 0);
      assert.deepEqual((await database.prepare('SELECT * FROM artifact_storage_usage ORDER BY scope_id').all()).results, storageBefore.results);
    } finally { database.connection.exec('DROP TRIGGER synthetic_publication_failure'); }
    const operation = crypto.randomUUID();
    const replies = await Promise.all([post(author, '/v1/contributions', uploadBody(first), operation),
      post(author, '/v1/contributions', uploadBody(first), operation)]);
    assert.deepEqual(expectStatus(replies[0]), expectStatus(replies[1]));
    expectStatus(await post(author, '/v1/contributions', uploadBody(second)));
    expectStatus(await read(stranger, `/v1/publication-operations/${operation}`), 404);
    assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM artifact_payloads').first('n'), 2);
    const evidence = matchEvidenceSchema.parse((await verifiedCase('ai')).artifact.summary.matching);
    for (const actor of ['', 'not-an-ip']) {
      expectStatus(await send(new Request(env.PUBLIC_ORIGIN + '/v1/discord/start', {
        headers: { 'CF-Connecting-IP': actor },
      })), 503);
    }
    const availability = () => post(reader, '/v1/availability/batch', { version: 1, items: [{ id: 'target', evidence }] });
    assert.equal(expectStatus(await availability()).items[0].candidateCount, 2);
    const search = (extra = {}) => post(reader, '/v1/matches', { version: 1, evidence, pageSize: 1, ...extra });
    const page = expectStatus(await search());
    assert.equal(page.candidates.length, 1); assert.ok(page.nextCursor);
    const next = expectStatus(await search({ cursor: page.nextCursor }));
    assert.equal(next.candidates.length, 1); assert.notEqual(next.candidates[0].revisionId, page.candidates[0].revisionId);
    assert.equal(next.nextCursor, null);
    assert.equal(page.candidates[0].compatibility.selected, false);
    assert.ok(!JSON.stringify(page).match(/discord|privateKey|object_key|findings/));
    const revision = first.submissionId, path = `/v1/contributions/${revision}`;
    const bundle = expectStatus(await read(reader, path + '/artifact'));
    assert.equal(bundle.artifact, base64(first.artifactBytes));
    assert.equal(bundle.manifest, base64(first.manifestBytes));
    assert.equal(bundle.envelope, base64(first.envelopeBytes));
    const keys = expectStatus(await read(reader, '/v1/receipt-keys')).keys;
    await verifyReceipt(bundle.receipt, { service: env.PUBLIC_ORIGIN, contributorId: reader.contributorId,
      revisionId: revision, artifactDigest: bundle.artifactDigest }, keys);
    const foreign = expectStatus(await read(stranger, path + '/artifact'));
    assert.notEqual(foreign.receipt.receipt.receiptId, bundle.receipt.receipt.receiptId);
    const stats = () => database.prepare('SELECT imports, likes, dislikes FROM contribution_statistics WHERE revision_id = ?').bind(revision).first();
    assert.deepEqual(await stats(), { imports: 0, likes: 0, dislikes: 0 });
    expectStatus(await post(reader, path + '/vote', { version: 1, vote: 'like' }), 403);
    if (current.importDownload) await current.importDownload({ bundle, keys, contributionId: first.contributionId,
      contributorId: reader.contributorId, installationId: reader.installationId, keyId: reader.keyId });
    const receiptBody = { version: 1, receipt: bundle.receipt }, receiptOperation = `import-${bundle.receipt.receipt.receiptId}`;
    const receipts = await Promise.all([post(reader, '/v1/import-receipts', receiptBody, receiptOperation),
      post(sibling, '/v1/import-receipts', receiptBody, receiptOperation)]);
    assert.deepEqual(expectStatus(receipts[0]), expectStatus(receipts[1]));
    expectStatus(await post(stranger, '/v1/import-receipts', receiptBody, receiptOperation), 400);
    const voteOperation = crypto.randomUUID(), vote = { version: 1, vote: 'like' };
    const votes = await Promise.all([post(reader, path + '/vote', vote, voteOperation), post(sibling, path + '/vote', vote, voteOperation)]);
    assert.deepEqual(expectStatus(votes[0]), expectStatus(votes[1]));
    assert.deepEqual(await stats(), { imports: 1, likes: 1, dislikes: 0 });
    assert.equal(expectStatus(await read(sibling, path + '/vote')).vote, 'like');
    assert.equal(expectStatus(await read(stranger, path + '/vote')).vote, 'neutral');
    expectStatus(await post(reader, path + '/vote', { version: 1, vote: 'dislike' }, voteOperation), 409);
    expectStatus(await search({ cursor: page.nextCursor }), 400);
    const reportOperation = crypto.randomUUID(), note = '<script>private synthetic report</script>';
    const report = { version: 1, reason: 'privacy', note };
    const reportResult = expectStatus(await post(reader, path + '/reports', report, reportOperation));
    assert.deepEqual(expectStatus(await post(sibling, path + '/reports', report, reportOperation)), reportResult);
    expectStatus(await post(stranger, path + '/reports', report), 403);
    expectStatus(await admin('/admin/v1/session'));
    expectStatus(await admin('/admin/v1/session', undefined, { 'Cf-Access-Jwt-Assertion': '' }), 401);
    expectStatus(await send(new Request(env.OPERATOR_ORIGIN + '/admin/')), 401);
    expectStatus(await admin('/admin/v1/moderation', {}, { Origin: 'https://hostile.test' }), 403);
    const inspect = (kind, id) => admin(`/admin/v1/moderation/${kind}/${id}`);
    async function moderate(command, kind, targetId, extra = {}) {
      const snapshot = expectStatus(await inspect(kind, targetId + (kind === 'vote' ? `/${extra.revisionId}` : '')));
      const body = { version: 1, actionId: crypto.randomUUID(), command, targetId,
        expectedRevision: snapshot.revision, reason: 'Synthetic reviewed evidence', ...extra };
      const applied = expectStatus(await admin('/admin/v1/moderation', body));
      assert.deepEqual(expectStatus(await admin('/admin/v1/moderation', body)), applied);
      expectStatus(await admin('/admin/v1/moderation', { ...body, actionId: crypto.randomUUID() }), 409);
      return applied;
    }
    assert.equal(expectStatus(await inspect('report', reportResult.result.reportId)).state.note, note);
    await moderate('quarantine', 'revision', revision);
    expectStatus(await read(reader, path + '/artifact'), 410);
    assert.equal(expectStatus(await availability()).items[0].candidateCount, 1);
    await moderate('restore', 'revision', revision);
    assert.equal(expectStatus(await read(reader, path + '/artifact')).artifact, bundle.artifact);
    assert.equal(expectStatus(await availability()).items[0].candidateCount, 2);
    await moderate('suspend', 'contributor', reader.contributorId);
    expectStatus(await read(sibling, '/v1/identity'), 401);
    await moderate('unsuspend', 'contributor', reader.contributorId);
    assert.equal(expectStatus(await read(sibling, path + '/vote')).vote, 'like');
    await moderate('exclude_vote', 'vote', reader.contributorId, { revisionId: revision });
    assert.deepEqual(await stats(), { imports: 1, likes: 0, dislikes: 0 });
    await moderate('restore_vote', 'vote', reader.contributorId, { revisionId: revision });
    assert.deepEqual(await stats(), { imports: 1, likes: 1, dislikes: 0 });
    await moderate('resolve_report', 'report', reportResult.result.reportId);
    for (const route of ['overview', 'contributions', 'contributors', 'operations', 'audit'])
      expectStatus(await admin('/admin/v1/dashboard/' + route));
    expectStatus(await admin(`/admin/v1/dashboard/contributors/${reader.contributorId}/keys`));
    expectStatus(await admin(`/admin/v1/dashboard/contributors/${reader.contributorId}/usage`));
    expectStatus(await admin('/admin/v1/reports'));
    const overview = expectStatus(await admin('/admin/v1/dashboard/overview'));
    assert.equal(overview.providerUsage.providerVerified, false);
    for (const handler of ['download', 'import', 'vote', 'report']) {
      const observation = overview.observations.find(row => row.handler === handler);
      assert.ok(observation.unknownReads > 0, `${handler} must not fabricate SQLite scanned rows`);
      assert.ok(observation.writes > 0);
    }
    assert.deepEqual(await stats(), { imports: 1, likes: 1, dislikes: 0 });
    const preserved = expectStatus(await read(reader, path + '/artifact'));
    assert.equal(preserved.artifact, bundle.artifact);
    assert.equal(preserved.manifest, bundle.manifest);
    assert.equal(preserved.envelope, bundle.envelope);
    await database.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
    expectStatus(await read(reader, path + '/artifact'));
    expectStatus(await post(reader, path + '/vote', { version: 1, vote: 'neutral' }));
    assert.deepEqual(await stats(), { imports: 1, likes: 0, dislikes: 0 });
    const requests = async key => [
      await signedPost(key, '/v1/availability/batch', JSON.stringify({ version: 1, items: [{ id: 'target', evidence }] }), crypto.randomUUID(), timestamp()),
      await signedPost(key, '/v1/matches', JSON.stringify({ version: 1, evidence, pageSize: 1 }), crypto.randomUUID(), timestamp()),
      ...await Promise.all([path + '/artifact', '/v1/receipt-keys', path + '/vote', path + '/votes']
        .map(route => signedRead(key, route, timestamp()))),
    ];
    const warmed = await requests(reader);
    const nonceCount = await database.prepare('SELECT COUNT(*) AS n FROM request_nonces').first('n');
    for (const request of warmed) {
      expectStatus(await send(request.clone())); expectStatus(await send(request.clone()));
    }
    assert.equal(await database.prepare('SELECT COUNT(*) AS n FROM request_nonces').first('n'), nonceCount);
    assert.deepEqual(await stats(), { imports: 1, likes: 0, dislikes: 0 });
    await database.prepare('UPDATE discord_membership SET checked_at = ? WHERE contributor_id = ?')
      .bind(timestamp() - 901, reader.contributorId).run();
    provider.mode = 'outage';
    for (const request of await requests(reader)) {
      assert.equal(expectStatus(await send(request), 503).error.code, 'membership_unavailable');
    }
    await database.prepare('UPDATE discord_membership_refresh SET retry_at = 0').run();
    provider.mode = 'nonmember';
    for (const request of await requests(reader)) {
      assert.equal(expectStatus(await send(request), 403).error.code, 'membership_ineligible');
    }
    await database.prepare('UPDATE discord_membership SET checked_at = ? WHERE contributor_id = ?')
      .bind(timestamp() - 901, reader.contributorId).run();
    provider.mode = 'member'; expectStatus(await read(reader, '/v1/identity'));
    const rotation = await preparedRotation(reader);
    expectStatus(await post(reader, '/v1/keys/rotate', rotation.body, rotation.operationId));
    for (const request of [...warmed, ...await requests(reader)]) {
      assert.equal(expectStatus(await send(request), 401).error.code, 'key_inactive');
    }
    for (const request of await requests(sibling)) expectStatus(await send(request));
    const revokedRequests = await requests(sibling);
    expectStatus(await post(rotation.replacement, '/v1/keys/revoke', {
      version: 1, installationId: sibling.installationId, keyId: sibling.keyId,
    }));
    for (const request of [...revokedRequests, ...await requests(sibling)]) {
      assert.equal(expectStatus(await send(request), 401).error.code, 'key_inactive');
    }
    assert.equal(expectStatus(await read(rotation.replacement, path + '/artifact')).artifact, bundle.artifact);
    assert.deepEqual(await stats(), { imports: 1, likes: 0, dislikes: 0 });
    const durableTables = ['contributors', 'installations', 'installation_keys', 'operations',
      'contribution_publications', 'contribution_revisions', 'contribution_parents', 'artifact_payloads',
      'artifact_storage_usage', 'import_receipts', 'revision_votes', 'contribution_statistics',
      'reports', 'moderation_actions', 'vote_exclusions', 'moderation_versions', 'moderation_epoch'];
    const snapshot = async () => {
      const rows = new Map();
      for (const table of durableTables) rows.set(table, (await database.prepare(`SELECT * FROM ${table} ORDER BY 1, 2`).all()).results);
      return rows;
    };
    const storage = async () => {
      const file = database.connection.prepare('PRAGMA database_list').get().file;
      const bytes = suffix => existsSync(file + suffix) ? statSync(file + suffix).size : 0;
      const payloadBytes = await database.prepare('SELECT COALESCE(SUM(length(artifact) + length(manifest) + length(envelope)), 0) AS bytes FROM artifact_payloads').first('bytes');
      assert.equal(await database.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'").first('bytes'), payloadBytes);
      return { databaseBytes: bytes(''), walBytes: bytes('-wal'), shmBytes: bytes('-shm'), payloadBytes };
    };
    const retained = await snapshot(), beforeStorage = await storage();
    await database.prepare('INSERT INTO request_nonces VALUES (?, ?, ?, ?)')
      .bind(rotation.replacement.keyId, 'expired-retention-nonce', timestamp() - 1, reader.contributorId).run();
    const plan = await previewRetention(database, Date.now());
    assert.equal(plan.policy.durableRecords, 'indefinite');
    assert.equal(plan.payloadBytesReclaimed, 0);
    assert.deepEqual(await snapshot(), retained);
    await current.service.scheduled({}, env);
    await current.service.scheduled({}, env);
    assert.deepEqual(await snapshot(), retained);
    assert.equal(await database.prepare("SELECT COUNT(*) AS n FROM request_nonces WHERE nonce = 'expired-retention-nonce'").first('n'), 0);
    for (const table of ['artifact_payloads', 'contribution_revisions', 'import_receipts', 'reports', 'moderation_actions']) {
      await assert.rejects(database.prepare(`DELETE FROM ${table}`).run());
    }
    assert.deepEqual(await snapshot(), retained);
    assert.deepEqual(expectStatus(await post(rotation.replacement, '/v1/import-receipts', receiptBody, receiptOperation)), expectStatus(receipts[0]));
    assert.deepEqual(expectStatus(await post(rotation.replacement, path + '/vote', vote, voteOperation)), expectStatus(votes[0]));
    assert.deepEqual(await stats(), { imports: 1, likes: 0, dislikes: 0 });
    const afterStorage = await storage();
    assert.equal(afterStorage.payloadBytes, beforeStorage.payloadBytes);
    console.log(JSON.stringify({ retentionEvidence: { policyVersion: plan.policy.version,
      artifacts: retained.get('artifact_payloads').length, before: beforeStorage, after: afterStorage,
      storageScope: 'synthetic_fixture_not_capacity_or_provider_billing' } }));
  }, runtime);
}
