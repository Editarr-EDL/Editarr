import { tunnelActor } from '../src/runtime/environment.mjs';
import { beforeAll, expect, it, vi } from 'vitest';
import migration from '../migrations/0012_import_receipts.sql?raw';
import membershipMigration from '../migrations/0007_membership.sql?raw';
import service from '../src/index';
import { acknowledgeImport, reconcileImportCounts } from '../src/contributions/imports';
import { base64, issueReceipt, loadReceiptSigner, type ReceiptSigner, type ReceiptKey } from '../src/contributions/receipts';
import { publicKeyId } from '../src/identity/rotation-proof';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { contributorImport } from '../src/http/imports';
import { fixtureKey } from './helpers/publication-signatures';
import { signedPost, signingKey, type SigningKey } from './helpers/key-lifecycle';
import { observeDownloadDatabase, recordImportUsage } from '../src/budgets/download-usage';
import contract from '../../tests/fixtures/community/import-acknowledgment-v1.json';
import { rawRevision } from './helpers/catalog';

let signer: ReceiptSigner, keys: ReceiptKey[], revisionId: string, digest: string;
let edgeKey: SigningKey;
beforeAll(async () => {
  await setupPublication();
  await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  await db.exec(membershipMigration.replace(/\s*\n\s*/g, ' '));
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  keys = [{ keyId: await publicKeyId(publicKey), publicKey: base64(publicKey), status: 'active' }];
  signer = await loadReceiptSigner(base64(new Uint8Array(await crypto.subtle.exportKey('pkcs8', pair.privateKey))), keys);
  const item = await publicationCase('ai');
  const published = await publishArtifact(db, item.identity, item.input, limits, now);
  revisionId = published.result.revisionId; digest = published.result.artifactDigest;
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('reader', 'discord-reader', 'Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('reader-install', 'reader', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('reader-key', 'reader-install', 'Ed25519', ?, 'active', 1, NULL)")
      .bind(new Uint8Array(32)),
  ]);
  edgeKey = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('edge-reader', '1554889314549243999', 'Edge Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('edge-install', 'edge-reader', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'edge-install', 'Ed25519', ?, 'active', 1, NULL)").bind(edgeKey.keyId, edgeKey.publicKey),
  ]);
});

async function attempt(contributorId = 'reader') {
  const receipt = await issueReceipt(signer, { service: 'https://community.example', contributorId, revisionId, artifactDigest: digest }, now);
  const body = new TextEncoder().encode(JSON.stringify({ version: 1, receipt }));
  const contentDigest = `sha-256=:${base64(new Uint8Array(await crypto.subtle.digest('SHA-256', body)))}:`;
  const identity = { contributorId, keyId: contributorId === 'reader' ? 'reader-key' : 'key-1', displayName: 'Reader',
    nonce: crypto.randomUUID(), operationId: `import-${receipt.receipt.receiptId}`, contentDigest, expiresAt: now + 60 };
  return { receipt, identity };
}

it('counts concurrent retries and restored intents once, excluding author imports', async () => {
  const item = await attempt();
  const results = await Promise.all(Array.from({ length: 4 }, () => acknowledgeImport(db,
    { ...item.identity, nonce: crypto.randomUUID() }, 'https://community.example', item.receipt, keys, now)));
  expect(results.every(result => result.counted)).toBe(true);
  expect(await acknowledgeImport(db, { ...item.identity, nonce: crypto.randomUUID() }, 'https://community.example', item.receipt, keys, now)).toEqual(results[0]);
  const author = await attempt('contributor-1');
  expect((await acknowledgeImport(db, author.identity, 'https://community.example', author.receipt, keys, now)).counted).toBe(false);
  expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(revisionId).first()).toEqual({ imports: 1 });
  expect(await db.prepare('SELECT count(*) AS total FROM import_receipts').first()).toEqual({ total: 2 });
});

it('rejects forgery, wrong contributor, digest, service, changed intent and revoked receipt keys', async () => {
  const item = await attempt();
  for (const receipt of [{ ...item.receipt, signature: base64(new Uint8Array(64)) },
    { ...item.receipt, receipt: { ...item.receipt.receipt, contributorId: 'other' } }]) {
    await expect(acknowledgeImport(db, item.identity, 'https://community.example', receipt, keys, now)).rejects.toThrow('invalid_download_receipt');
  }
  const wrongDigest = await issueReceipt(signer, { ...item.receipt.receipt, artifactDigest: '0'.repeat(64) }, now);
  await expect(acknowledgeImport(db, { ...item.identity, operationId: `import-${wrongDigest.receipt.receiptId}` },
    'https://community.example', wrongDigest, keys, now)).rejects.toThrow('invalid_import_receipt');
  await expect(acknowledgeImport(db, item.identity, 'https://wrong.example', item.receipt, keys, now)).rejects.toThrow('invalid_download_receipt');
  await expect(acknowledgeImport(db, { ...item.identity, operationId: 'different-operation' }, 'https://community.example', item.receipt, keys, now)).rejects.toThrow('invalid_import_receipt');
  await expect(acknowledgeImport(db, item.identity, 'https://community.example', item.receipt,
    keys.map(key => ({ ...key, status: 'revoked' })), now)).rejects.toThrow('invalid_download_receipt');
});

it('reconciles import counters from durable facts without changing votes or facts', async () => {
  await db.prepare('UPDATE contribution_statistics SET imports = 99, likes = 3 WHERE revision_id = ?').bind(revisionId).run();
  await reconcileImportCounts(db, [revisionId]);
  expect(await db.prepare('SELECT imports, likes FROM contribution_statistics WHERE revision_id = ?').bind(revisionId).first()).toEqual({ imports: 1, likes: 3 });
  await expect(db.prepare('DELETE FROM import_receipts').run()).rejects.toThrow('immutable_import_receipt');
});

it('accepts a separately issued receipt and a sibling installation without recounting or rewriting facts', async () => {
  const item = await attempt();
  await db.batch([
    db.prepare("INSERT INTO installations VALUES ('sibling-install', 'reader', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('sibling-key', 'sibling-install', 'Ed25519', ?, 'active', 1, NULL)").bind(new Uint8Array(32)),
  ]);
  const later = await issueReceipt(signer, item.receipt.receipt, now + 10);
  expect((await acknowledgeImport(db, { ...item.identity, keyId: 'sibling-key', nonce: crypto.randomUUID() },
    'https://community.example', later, keys, now + 10)).counted).toBe(true);
  expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(revisionId).first()).toEqual({ imports: 1 });
});

it('gates signed HTTP acknowledgments by current membership, key state and bounded input', async () => {
  const author = await attempt('contributor-1');
  const request = async () => signedPost(await fixtureKey(), '/v1/import-receipts', JSON.stringify({ version: 1, receipt: author.receipt }), author.identity.operationId);
  const handle = (value: Request, membership = async () => 'eligible' as const, requestBytes = 8192) =>
    contributorImport(value, db, 'community.example', now, membership, { requestBytes, maxQueries: 32 }, keys);
  expect((await handle(await request())).status).toBe(200);
  expect((await handle(await request(), async () => 'ineligible' as never)).status).toBe(403);
  expect((await handle(await request(), async () => 'unavailable' as never)).status).toBe(503);
  expect((await handle(await request(), undefined, 10)).status).toBe(413);
  expect((await handle(new Request('https://community.example/v1/import-receipts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }))).status).toBe(401);
  await db.prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = 'key-1'").bind(now).run();
  try { expect((await handle(await request())).status).toBe(401); }
  finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run(); }
});

it('observes malformed signed requests without charging an allowance', async () => {
  const author = await attempt('contributor-1');
  const before = await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>();
  const malformed = await signedPost(await fixtureKey(), '/v1/import-receipts', '{', author.identity.operationId);
  expect((await contributorImport(malformed, db, 'community.example', now, async () => 'eligible', { requestBytes: 8192, maxQueries: 32 }, keys)).status).toBe(400);
  expect((await db.prepare("SELECT occurrences AS used FROM usage_observations WHERE contributor_id = 'contributor-1' AND resource = 'worker_requests' AND outcome = 'denied'").first<{ used: number }>())!.used).toBe((before?.used ?? 0) + 1);
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
  try {
    const request = await signedPost(await fixtureKey(), '/v1/import-receipts', JSON.stringify({ version: 1, receipt: author.receipt }), author.identity.operationId);
    expect((await contributorImport(request, db, 'community.example', now, async () => 'eligible', { requestBytes: 8192, maxQueries: 32 }, keys)).status).toBe(200);
  } finally { await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run(); }
});

it('atomically rolls back a replayed nonce on another revision and counts revisions independently', async () => {
  const item = await publicationCase('manual');
  const published = await publishArtifact(db, item.identity, item.input, limits, now);
  const receipt = await issueReceipt(signer, { service: 'https://community.example', contributorId: 'reader',
    revisionId: published.result.revisionId, artifactDigest: published.result.artifactDigest }, now);
  const identity = { ...(await attempt()).identity, operationId: `import-${receipt.receipt.receiptId}` };
  const usedNonce = await db.prepare("SELECT nonce FROM request_nonces WHERE key_id = 'reader-key' LIMIT 1").first<{ nonce: string }>();
  await expect(acknowledgeImport(db, { ...identity, nonce: usedNonce!.nonce }, 'https://community.example', receipt, keys, now)).rejects.toThrow('request_nonces');
  expect(await db.prepare('SELECT count(*) AS total FROM import_receipts WHERE revision_id = ?').bind(receipt.receipt.revisionId).first()).toEqual({ total: 0 });
  expect(await db.prepare("SELECT count(*) AS total FROM operations WHERE kind = 'receipt' AND operation_id = ?").bind(identity.operationId).first()).toEqual({ total: 0 });
  expect((await acknowledgeImport(db, identity, 'https://community.example', receipt, keys, now)).counted).toBe(true);
  expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(receipt.receipt.revisionId).first()).toEqual({ imports: 1 });
});

it('records measured first/retry costs and preserves unknown observation metadata', async context => {
  const receipt = await issueReceipt(signer, { service: 'https://community.example', contributorId: 'edge-reader', revisionId, artifactDigest: digest }, now);
  const costs = [];
  for (let attemptIndex = 0; attemptIndex < 2; attemptIndex++) {
    const observer = observeDownloadDatabase(db, 64);
    const request = await signedPost(edgeKey, '/v1/import-receipts', JSON.stringify({ version: 1, receipt }), `import-${receipt.receipt.receiptId}`);
    expect((await contributorImport(request, observer.db, 'community.example', now, async () => 'eligible', { requestBytes: 8192, maxQueries: 32 }, keys)).status).toBe(200);
    costs.push(observer.snapshot());
  }
  Object.assign(context.task.meta, { measurement: { scope: 'local SQLite handler only', first: costs[0], retry: costs[1] } });
  expect(costs[0].rowsWritten).toBeGreaterThan(costs[1].rowsWritten!);
  expect((await db.prepare("SELECT * FROM quota_charges WHERE contributor_id = 'edge-reader'").all()).results).toEqual([]);
  const before = await db.prepare("SELECT unknown_reads FROM import_usage_observations WHERE contributor_id = 'edge-reader' AND outcome = 'allowed'").first<{ unknown_reads: number }>();
  await recordImportUsage(db, 'edge-reader', 'allowed', now, { rowsRead: null, rowsWritten: null });
  expect(await db.prepare("SELECT unknown_reads, unknown_writes FROM import_usage_observations WHERE contributor_id = 'edge-reader'").first()).toEqual({ unknown_reads: before!.unknown_reads + 1, unknown_writes: 1 });
});

it('wires native admission, continuing membership and explicit configuration without a private signer', async () => {
  const timestamp = Math.floor(Date.now() / 1000);
  const receipt = await issueReceipt(signer, { service: 'https://community.example', contributorId: 'edge-reader', revisionId, artifactDigest: digest }, now + 1);
  const provider = vi.fn(async () => Response.json({ user: { id: '1554889314549243999' } }));
  vi.stubGlobal('fetch', provider);
  const limiter = { limit: vi.fn(async () => ({ success: true })) };
  const config = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
    IMPORT_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 32 }), DOWNLOAD_RECEIPT_KEYS: JSON.stringify(keys),
    DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-token'};
  async function request() {
    const signed = await signedPost(edgeKey, '/v1/import-receipts', JSON.stringify({ version: 1, receipt }), `import-${receipt.receipt.receiptId}`, timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  try {
    expect((await service.fetch(await request(), config)).status).toBe(200);
    expect(provider).toHaveBeenCalledOnce();
    await db.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp - 900).run();
    provider.mockImplementation(async () => { throw new Error('synthetic offline'); });
    expect((await service.fetch(await request(), config)).status).toBe(503);
    expect((await service.fetch(await request(), { ...config, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect((await service.fetch(await request(), { ...config, IMPORT_LIMITS: undefined })).status).toBe(503);
    expect((await service.fetch(await request(), { ...config, APP_ENV: 'production' })).status).toBe(503);
  } finally { vi.unstubAllGlobals(); }
});

it('returns the frozen cross-language acknowledgment contract under a real signed HTTP request', async () => {
  const key = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO works VALUES ('vector-work', 'movie', 1)"),
    db.prepare("INSERT INTO releases VALUES ('vector-release', 'vector-work', '{}', NULL, NULL, NULL, 1)"),
    db.prepare("INSERT INTO contributors VALUES ('reader-vector', 'discord-vector', 'Vector Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('vector-install', 'reader-vector', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'vector-install', 'Ed25519', ?, 'active', 1, NULL)").bind(key.keyId, key.publicKey),
  ]);
  await rawRevision('revision-vector', 'vector-release');
  const request = await signedPost(key, contract.path, JSON.stringify(contract.request), contract.operationId);
  const output = await contributorImport(request, db, 'community.example', now, async () => 'eligible',
    { requestBytes: 8192, maxQueries: 32 }, [{ keyId: contract.request.receipt.receipt.serviceKeyId, publicKey: contract.publicKey, status: 'active' }]);
  expect(output.status).toBe(200);
  expect(await output.json()).toEqual(contract.response);
});