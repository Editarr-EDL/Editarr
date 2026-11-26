import { beforeAll, describe, expect, it, vi } from 'vitest';
import fixture from './fixtures/community-v3/master-snapshot.json';
import { tunnelActor } from '../src/runtime/environment.mjs';
import service from '../src/index';
import { contributorMasterRead } from '../src/http/master';
import { DEFAULT_SUBMISSION_LIMITS } from '../src/submissions/input';
import { snapshotBytes } from '../src/filters';
import { issueMasterReceipt, receiptIdentity, receiptSigningBytes, signSnapshot, snapshotSigningBytes,
  verifyServiceSignature } from '../src/master/signing';
import { loadReceiptSigner, type ReceiptSigner } from '../src/security/service-signer';
import { contributorSubmission } from '../src/http/submissions';
import { SNAPSHOT_RETENTION, supersededSnapshots } from '../src/master/recompute';
import { PRUNE_SNAPSHOT_BYTES_SQL, pruneSnapshotBytes } from '../src/retention/windows';
import { artifact, contributor, encode, nonce, submitRequest, type Contributor } from './helpers/submissions';
import { signedRead } from './helpers/key-lifecycle';
import { db, now, policy, setupVersionCatalog } from './helpers/versions';

const seed = Buffer.from(fixture.servicePrivateKeySeedHex, 'hex');
const pkcs8 = Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), seed]).toString('base64');
const keys = fixture.receiptKeys as { keyId: string; publicKey: string; status: 'active' | 'retired' | 'revoked' }[];
const eligible = async () => 'eligible' as const;
let signer: ReceiptSigner, alice: Contributor, bob: Contributor, versionId: string;
const read = async (who: Contributor, target: string, checks = policy.versionChecksPerHour) => {
  const response = await contributorMasterRead(await signedRead(who, target, now, nonce()), db, 'community.example', now, eligible,
    { receiptKeys: keys, signer, versionChecksPerHour: checks });
  return { status: response.status, headers: response.headers, body: await response.json() as Record<string, any> };
};
const submit = async (who: Contributor, tmdb: string, aliasId = 'theatrical') => {
  const value = artifact(who, { work: { ...artifact(who).work, tmdb }, version: { choice: 'alias', aliasId } });
  const response = await contributorSubmission(await submitRequest(who, await encode(value, who)),
    db, 'community.example', now, eligible, { policy, limits: DEFAULT_SUBMISSION_LIMITS });
  return (await response.json() as { result: Record<string, any> }).result;
};
const receipts = async () => (await db.prepare('SELECT count(*) AS n FROM download_receipts').first<{ n: number }>())!.n;

beforeAll(async () => {
  await setupVersionCatalog([]);
  signer = await loadReceiptSigner(pkcs8, keys);
  [alice, bob] = await Promise.all([contributor(), contributor()]);
  versionId = (await submit(alice, '200')).versionId;
});

describe('master snapshot signing vectors (§7.3)', () => {
  it('reproduces the shared snapshot bytes, signatures, receipt ID and signed bytes exactly', async () => {
    const { request, response } = fixture;
    const bytes = new TextEncoder().encode(fixture.snapshotJson);
    expect(Buffer.from(await crypto.subtle.digest('SHA-256', bytes)).toString('hex')).toBe(fixture.snapshotDigest);
    expect(Buffer.from(bytes).toString('base64')).toBe(response.snapshot);
    const { revision, generatedAt, ...content } = JSON.parse(fixture.snapshotJson) as Record<string, number>;
    expect(new TextDecoder().decode(snapshotBytes(JSON.stringify(content), revision, generatedAt))).toBe(fixture.snapshotJson);
    const binding = { service: request.service, versionId: request.versionId, revision, snapshotDigest: fixture.snapshotDigest };
    const issuedAt = response.snapshotSignature.issuedAt;
    expect(await signSnapshot(signer, binding, issuedAt)).toEqual(response.snapshotSignature);
    const { signature: _signature, ...unsigned } = response.snapshotSignature;
    expect(Buffer.from(snapshotSigningBytes(unsigned as never)).toString('hex')).toBe(fixture.snapshotSigningBytesHex);
    expect(await receiptIdentity({ ...binding, contributorId: request.contributorId })).toBe(fixture.receiptId);
    const receipt = await issueMasterReceipt(signer, { ...binding, contributorId: request.contributorId }, issuedAt);
    expect(receipt).toEqual(response.receipt);
    expect(Buffer.from(receiptSigningBytes(receipt.receipt)).toString('hex')).toBe(fixture.receiptSigningBytesHex);
    expect(await verifyServiceSignature(keys, receipt.receipt.serviceKeyId, receiptSigningBytes(receipt.receipt), receipt.signature)).toBe(true);
  });
});

describe('GET /v1/versions/{versionId}/master', () => {
  it('serves a signed snapshot with a receipt bound to the caller, version, revision and digest', async () => {
    const reply = await read(bob, `/v1/versions/${versionId}/master`);
    expect(reply.status).toBe(200);
    expect(reply.headers.get('cache-control')).toBe('private, no-store');
    const bytes = Buffer.from(reply.body.snapshot, 'base64');
    const digest = Buffer.from(await crypto.subtle.digest('SHA-256', bytes)).toString('hex');
    const snapshot = JSON.parse(bytes.toString()) as { versionId: string; revision: number; service: string; filters: { filterId: string }[] };
    expect(snapshot).toMatchObject({ schemaVersion: 'community-master-snapshot-1', service: 'https://community.example', versionId, revision: 1 });
    expect(reply.body.snapshotSignature).toMatchObject({ versionId, revision: 1, snapshotDigest: digest, issuedAt: now, serviceKeyId: signer.keyId });
    const { signature, ...unsigned } = reply.body.snapshotSignature;
    expect(await verifyServiceSignature(keys, signer.keyId, snapshotSigningBytes(unsigned), signature)).toBe(true);
    const receipt = reply.body.receipt.receipt;
    expect(receipt).toMatchObject({ contributorId: bob.contributorId, versionId, revision: 1, snapshotDigest: digest,
      receiptId: await receiptIdentity({ service: 'https://community.example', contributorId: bob.contributorId, versionId, revision: 1, snapshotDigest: digest }) });
    expect(await verifyServiceSignature(keys, signer.keyId, receiptSigningBytes(receipt), reply.body.receipt.signature)).toBe(true);
    expect(await db.prepare('SELECT contributor_id, version_id, revision, snapshot_digest FROM download_receipts WHERE receipt_id = ?')
      .bind(receipt.receiptId).first()).toEqual({ contributor_id: bob.contributorId, version_id: versionId, revision: 1, snapshot_digest: digest });
    expect((await db.prepare('SELECT filter_id FROM master_revision_filters WHERE version_id = ? AND revision = 1').bind(versionId).all()).results)
      .toEqual(snapshot.filters.map(filter => ({ filter_id: filter.filterId })));
    const again = await read(bob, `/v1/versions/${versionId}/master?knownRevision=0`);
    expect(again.body.receipt.receipt.receiptId).toBe(receipt.receiptId);
  });

  it('answers unchanged without a receipt when knownRevision is current or newer', async () => {
    const before = await receipts();
    for (const known of ['1', '7']) {
      const reply = await read(alice, `/v1/versions/${versionId}/master?knownRevision=${known}`);
      expect(reply.body).toEqual({ version: 1, changed: false, versionId, revision: 1 });
    }
    expect(await receipts()).toBe(before);
  });

  it('returns 404, 409 with mergedInto and 410 for missing, merged and quarantined versions', async () => {
    expect((await read(alice, '/v1/versions/v_missing/master')).body).toEqual({ error: { code: 'version_not_found' } });
    const target = (await submit(alice, '201')).versionId;
    const merged = (await submit(bob, '201', 'extended')).versionId;
    await db.prepare("UPDATE versions SET status = 'merged_into', merged_into = ? WHERE version_id = ?").bind(target, merged).run();
    expect(await read(alice, `/v1/versions/${merged}/master`)).toMatchObject({ status: 409,
      body: { error: { code: 'version_merged', mergedInto: target } } });
    await db.prepare('UPDATE versions SET quarantined = 1 WHERE version_id = ?').bind(target).run();
    expect(await read(alice, `/v1/versions/${target}/master`)).toMatchObject({ status: 410, body: { error: { code: 'version_unavailable' } } });
  });

  it.each(['?knownRevision=01', '?knownRevision=-1', '?knownRevision=x', '?knownRevision=1&knownRevision=2', '?other=1'])(
    'rejects %s with invalid_master_request', async query => {
      expect(await read(alice, `/v1/versions/${versionId}/master${query}`)).toMatchObject({ status: 400,
        body: { error: { code: 'invalid_master_request' } } });
    });

  it('limits version checks per contributor and hour, including unchanged answers', async () => {
    const carol = await contributor();
    expect((await read(carol, `/v1/versions/${versionId}/master?knownRevision=9`, 2)).status).toBe(200);
    expect((await read(carol, `/v1/versions/${versionId}/master`, 2)).status).toBe(200);
    const limited = await read(carol, `/v1/versions/${versionId}/master`, 2);
    expect([limited.status, limited.body]).toEqual([429, { error: { code: 'rate_limited' } }]);
    expect(Number(limited.headers.get('retry-after'))).toBe(3600 - (now % 3600));
  });

  it('is routed with the service signer and fails closed without it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ user: { id: alice.discordId } })));
    const env = { APP_ENV: 'local', CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
      TRUSTED_ACTOR_KEY: tunnelActor, DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token',
      MASTER_POLICY: JSON.stringify(policy), DOWNLOAD_RECEIPT_KEYS: JSON.stringify(keys), DOWNLOAD_RECEIPT_PRIVATE_KEY: pkcs8 };
    const edge = async () => {
      const signed = await signedRead(alice, `/v1/versions/${versionId}/master?knownRevision=1`, Math.floor(Date.now() / 1000), nonce());
      const headers = new Headers(signed.headers);
      headers.set('CF-Connecting-IP', '192.0.2.1');
      return new Request(signed, { headers });
    };
    try {
      expect(await (await service.fetch(await edge(), env)).json()).toEqual({ version: 1, changed: false, versionId, revision: 1 });
      expect((await service.fetch(await edge(), { ...env, DOWNLOAD_RECEIPT_PRIVATE_KEY: undefined })).status).toBe(503);
      const keyHistory = await service.fetch(await (async () => {
        const signed = await signedRead(alice, '/v1/receipt-keys', Math.floor(Date.now() / 1000), nonce());
        const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
        return new Request(signed, { headers });
      })(), env);
      expect(await keyHistory.json()).toEqual({ version: 1, service: 'https://community.example', keys });
    } finally { vi.unstubAllGlobals(); }
  });
});

describe('retained snapshot bytes (§8)', () => {
  it('keeps the latest snapshot, at most eight older receipted ones, and drops them after the receipt window', async () => {
    const versionId = (await submit(alice, '210')).versionId;
    const bytes = new TextEncoder().encode('{}');
    for (let revision = 2; revision <= 12; revision++) await db.batch([
      db.prepare(`INSERT INTO master_revisions (version_id, revision, snapshot_digest, generated_at, snapshot_bytes)
        VALUES (?, ?, ?, ?, ?)`).bind(versionId, revision, 'c'.repeat(64), now, bytes),
      db.prepare(`INSERT INTO download_receipts VALUES (?, ?, ?, ?, ?, 'key', ?)`)
        .bind(revision.toString(16).padStart(64, '0'), bob.contributorId, versionId, revision, 'c'.repeat(64), now),
    ]);
    const kept = async () => (await db.prepare(`SELECT revision FROM master_revisions WHERE version_id = ? AND snapshot_bytes IS NOT NULL
      ORDER BY revision`).bind(versionId).all<{ revision: number }>()).results.map(row => row.revision);
    await supersededSnapshots(db, versionId, 12, now).run();
    expect(await kept()).toEqual([4, 5, 6, 7, 8, 9, 10, 11, 12]);
    await pruneSnapshotBytes(db, (now + SNAPSHOT_RETENTION.receiptSeconds + 1) * 1000, 1000);
    expect(await kept()).toEqual([12]);
  });

  it('checks receipts through the version index instead of scanning download_receipts', async () => {
    const plan = (await db.prepare(`EXPLAIN QUERY PLAN ${PRUNE_SNAPSHOT_BYTES_SQL}`).bind(0, 1).all<{ detail: string }>())
      .results.map(row => row.detail);
    expect(plan.some(detail => detail.includes('idx_download_receipts_version'))).toBe(true);
    expect(plan.some(detail => /^SCAN r\b/.test(detail))).toBe(false);
  });
});

