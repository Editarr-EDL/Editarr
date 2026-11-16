import { tunnelActor } from '../src/runtime/environment.mjs';
import type { SqlStatement, SqlResult } from '../src/runtime/contracts';
import { beforeAll, expect, it, vi } from 'vitest';
import usage from '../migrations/0011_download_usage.sql?raw';
import membershipMigration from '../migrations/0007_membership.sql?raw';
import service from '../src/index';
import { contributorDownload } from '../src/http/download';
import { downloadLimits } from '../src/contributions/download';
import { base64, loadReceiptSigner, verifyReceipt, type ReceiptSigner, type ReceiptKey } from '../src/contributions/receipts';
import { publicKeyId } from '../src/identity/rotation-proof';
import { publishArtifact } from '../src/contributions/publication';
import { fixtureKey, syntheticPublication } from './helpers/publication-signatures';
import { signedRead, signingKey, type SigningKey } from './helpers/key-lifecycle';
import { db, now, limits, setupPublication } from './helpers/publication';

const caps = downloadLimits({ requestBytes: 8192, artifactBytes: 65536, manifestBytes: 16384, payloadBytes: 82176,
  responseBytes: 131072, maxQueries: 24, cacheEntries: 4, cacheBytes: 1048576, cacheTtlSeconds: 60 });
let signer: ReceiptSigner, keys: ReceiptKey[], secret: string, reader: SigningKey, input: Awaited<ReturnType<typeof syntheticPublication>>;
const eligible = async () => 'eligible' as const;
const request = async () => signedRead(await fixtureKey(), `/v1/contributions/${input.submissionId}/artifact`);
const handle = (request: Request, membership = eligible, bounds = caps) =>
  contributorDownload(request, db, 'community.example', now, membership, bounds, signer, db, keys);
beforeAll(async () => {
  await setupPublication(); await db.exec(usage.replace(/\s*\n\s*/g, ' '));
  await db.exec(membershipMigration.replace(/\s*\n\s*/g, ' '));
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  keys = [{ keyId: await publicKeyId(publicKey), publicKey: base64(publicKey), status: 'active' }];
  secret = base64(new Uint8Array(await crypto.subtle.exportKey('pkcs8', pair.privateKey)));
  signer = await loadReceiptSigner(secret, keys);
  reader = await signingKey();
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('reader-2', '1554889314549243999', 'Second Reader', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('reader-installation', 'reader-2', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES (?, 'reader-installation', 'Ed25519', ?, 'active', 1, NULL)").bind(reader.keyId, reader.publicKey),
  ]);
  input = await syntheticPublication();
  await publishArtifact(db, { contributorId: 'contributor-1', keyId: 'key-1', displayName: 'Public Name', nonce: crypto.randomUUID(),
    operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 }, input, limits, now);
});

it('returns the original gzip/manifest/signature and a trusted author key and contributor-bound receipt', async () => {
  const output = await handle(await request());
  expect(output.status).toBe(200);
  expect(output.headers.has('Content-Encoding')).toBe(false);
  const bytes = new Uint8Array(await output.arrayBuffer());
  expect(output.headers.get('Content-Length')).toBe(String(bytes.length));
  expect(output.headers.get('Content-Digest')).toBe(`sha-256=:${base64(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))}:`);
  const result = JSON.parse(new TextDecoder().decode(bytes));
  expect(result.artifact).toBe(base64(input.artifactBytes));
  expect(result.manifest).toBe(base64(input.manifestBytes));
  expect(result.envelope).toBe(base64(input.envelopeBytes));
  expect(result.historicalKey).toMatchObject({ contributorId: 'contributor-1', keyId: 'key-1', status: 'active' });
  expect(await verifyReceipt(result.receipt, { service: 'https://community.example', contributorId: 'contributor-1',
    revisionId: input.submissionId, artifactDigest: result.artifactDigest }, keys)).toEqual(result.receipt);
  expect(JSON.stringify(result)).not.toMatch(/object_key|artifacts\/|discord|privateKey|quota/);
  expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(input.submissionId).first()).toEqual({ imports: 0 });
  expect(await db.prepare("SELECT occurrences, unknown_reads FROM download_usage_observations WHERE outcome = 'allowed'").first())
    .toEqual({ occurrences: 1, unknown_reads: 1 });
});

it('denies nonmembers, invalid signatures and size/query bounds instead of returning payloads', async () => {
  expect((await handle(await request(), async () => 'ineligible' as never)).status).toBe(403);
  expect((await handle(await request(), async () => 'unavailable' as never)).status).toBe(503);
  expect((await handle(new Request((await request()).url))).status).toBe(401);
  expect((await handle(await request(), eligible, { ...caps, responseBytes: 10 })).status).toBe(413);
  expect((await handle(await request(), eligible, { ...caps, maxQueries: 1 })).status).toBe(413);
});

it('refreshes signed public receipt-key history behind membership checks without serving artifacts or private keys', async () => {
  const read = () => signedRead(reader, '/v1/receipt-keys');
  const spy = vi.spyOn(db, 'prepare');
  try {
    const output = await handle(await read());
    expect(output.status).toBe(200);
    const bytes = new Uint8Array(await output.arrayBuffer());
    expect(output.headers.get('Content-Length')).toBe(String(bytes.length));
    expect(output.headers.get('Content-Digest')).toBe(`sha-256=:${base64(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)))}:`);
    expect(output.headers.has('Content-Encoding')).toBe(false);
    expect(JSON.parse(new TextDecoder().decode(bytes))).toEqual({ version: 1, service: 'https://community.example', keys });
    expect(new TextDecoder().decode(bytes)).not.toContain(secret);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(false);
    const changed = keys.map(key => ({ ...key, status: 'revoked' as const }));
    const refreshed = await contributorDownload(await read(), db, 'community.example', now, eligible, caps, signer, db, changed);
    expect(await refreshed.json()).toEqual({ version: 1, service: 'https://community.example', keys: changed });
    expect((await handle(await read(), async () => 'ineligible' as never)).status).toBe(403);
    expect((await handle(new Request('https://community.example/v1/receipt-keys'))).status).toBe(401);
    expect((await handle(await read(), eligible, { ...caps, responseBytes: 10 })).status).toBe(413);
  } finally { spy.mockRestore(); }
});

it('denies warm artifact caches when the author is suspended and permits verified restoration', async () => {
  const read = () => signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`);
  expect((await handle(await read())).status).toBe(200);
  await db.prepare("UPDATE contributors SET state = 'suspended' WHERE contributor_id = 'contributor-1'").run();
  try { expect((await handle(await read())).status).toBe(410); }
  finally { await db.prepare("UPDATE contributors SET state = 'active' WHERE contributor_id = 'contributor-1'").run(); }
  expect((await handle(await read())).status).toBe(200);
});

it('reuses immutable bytes across contributors without reusing receipts or increasing imports', async () => {
  const first = await (await handle(await request())).json() as { receipt: { receipt: { contributorId: string; receiptId: string } } };
  const prepare = db.prepare.bind(db);
  const spy = vi.spyOn(db, 'prepare').mockImplementation(prepare);
  try {
    const output = await handle(await signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`));
    expect(output.status).toBe(200);
    const second = await output.json() as { receipt: { receipt: { contributorId: string; receiptId: string } } };
    expect(second.receipt.receipt.contributorId).toBe('reader-2');
    expect(second.receipt.receipt.receiptId).not.toBe(first.receipt.receipt.receiptId);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(false);
    expect(await db.prepare('SELECT imports FROM contribution_statistics WHERE revision_id = ?').bind(input.submissionId).first()).toEqual({ imports: 0 });
  } finally { spy.mockRestore(); }
});

it('honors reduced cache byte limits and TTLs instead of retaining older configuration authority', async () => {
  const prepare = db.prepare.bind(db);
  const spy = vi.spyOn(db, 'prepare').mockImplementation(prepare);
  try {
    expect((await handle(await request(), eligible, { ...caps, cacheBytes: 1 })).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(true);
    await handle(await request());
    spy.mockClear();
    const later = await signedRead(await fixtureKey(), `/v1/contributions/${input.submissionId}/artifact`, now + 2);
    expect((await contributorDownload(later, db, 'community.example', now + 2, eligible,
      { ...caps, cacheTtlSeconds: 1 }, signer, db)).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(true);
  } finally { spy.mockRestore(); }
});

it('keeps concurrent read retries contributor-bound without import side effects or HTTP gzip negotiation', async () => {
  const first = await request(), headers = new Headers(first.headers); headers.set('Accept-Encoding', 'gzip, br');
  const outputs = await Promise.all([handle(new Request(first, { headers })), handle(await request()),
    handle(await signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`))]);
  for (const output of outputs) { expect(output.status).toBe(200); expect(output.headers.has('Content-Encoding')).toBe(false); }
  const results = await Promise.all(outputs.map(output => output.json())) as { artifact: string;
    receipt: { receipt: { contributorId: string; receiptId: string } } }[];
  expect(results.every(result => result.artifact === base64(input.artifactBytes))).toBe(true);
  expect(results[0].receipt.receipt.receiptId).toBe(results[1].receipt.receipt.receiptId);
  expect(results[2].receipt.receipt.contributorId).toBe('reader-2');
  expect(results[2].receipt.receipt.receiptId).not.toBe(results[0].receipt.receipt.receiptId);
  expect(await db.prepare('SELECT imports, likes, dislikes FROM contribution_statistics WHERE revision_id = ?').bind(input.submissionId).first())
    .toEqual({ imports: 0, likes: 0, dislikes: 0 });
});

function intercept(sqlPart: string, transform: (result: SqlResult) => Promise<void>) {
  const prepare = db.prepare.bind(db);
  function tracked(statement: SqlStatement): SqlStatement {
    return new Proxy(statement, { get(target, property) {
      if (property === 'bind') return (...values: unknown[]) => tracked(target.bind(...values));
      if (property === 'all') return async () => { const result = await target.all(); await transform(result); return result; };
      const value = Reflect.get(target, property);
      return typeof value === 'function' ? value.bind(target) : value;
    } });
  }
  return vi.spyOn(db, 'prepare').mockImplementation(sql => sql.includes(sqlPart) ? tracked(prepare(sql)) : prepare(sql));
}

it('rejects missing, corrupted, wrong-length and wrong-digest payload rows without issuing a response receipt', async () => {
  for (const fault of ['missing', 'corrupt', 'length', 'digest']) {
    const spy = intercept('FROM artifact_payloads', async result => {
      const row = result.results[0] as { artifact: ArrayBuffer; manifest: ArrayBuffer; artifact_digest: string };
      if (fault === 'missing') result.results = [];
      else if (fault === 'corrupt') new Uint8Array(row.artifact)[0] ^= 1;
      else if (fault === 'length') row.manifest = row.manifest.slice(1);
      else row.artifact_digest = '0'.repeat(64);
    });
    try {
      const output = await contributorDownload(await request(), db, 'community.example', now, eligible, caps, signer, {});
      expect(output.status).toBe(503);
      expect(await output.json()).toEqual({ error: { code: 'artifact_unavailable' } });
    } finally { spy.mockRestore(); }
  }
});

it('rejects cached withdrawals/quarantines and permits retired author signatures only for an active reader', async () => {
  for (const state of ['withdrawn', 'quarantined']) {
    await db.prepare('UPDATE contribution_publications SET state = ?, updated_at = ? WHERE revision_id = ?').bind(state, now + 1, input.submissionId).run();
    try { expect((await handle(await request())).status).toBe(410); }
    finally { await db.prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = ?").bind(input.submissionId).run(); }
  }
  await db.prepare("UPDATE installation_keys SET state = 'retired', retired_at = ? WHERE key_id = 'key-1'").bind(now).run();
  try {
    expect((await handle(await request())).status).toBe(401);
    const output = await handle(await signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`));
    expect(output.status).toBe(200);
    expect(await output.json()).toMatchObject({ historicalKey: { status: 'retired' }, receipt: { receipt: { contributorId: 'reader-2' } } });
    await db.prepare("UPDATE installation_keys SET state = 'revoked' WHERE key_id = 'key-1'").run();
    expect((await handle(await signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`))).status).toBe(410);
  } finally { await db.prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run(); }
});

it('fences moderation and requester revocation raced against payload retrieval', async () => {
  const prepare = db.prepare.bind(db);
  for (const fault of ['moderation', 'requester']) {
    const spy = intercept('FROM artifact_payloads', async () => {
      if (fault === 'moderation') await prepare("UPDATE contribution_publications SET state = 'quarantined', updated_at = ? WHERE revision_id = ?").bind(now + 2, input.submissionId).run();
      else await prepare("UPDATE installation_keys SET state = 'revoked', retired_at = ? WHERE key_id = 'key-1'").bind(now).run();
    });
    try {
      const output = await contributorDownload(await request(), db, 'community.example', now, eligible, caps, signer, {});
      expect(output.status).toBe(fault === 'moderation' ? 410 : 401);
      expect(JSON.stringify(await output.json())).not.toContain('receiptId');
    } finally {
      spy.mockRestore();
      await prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = ? AND state != 'published'").bind(input.submissionId).run();
      await prepare("UPDATE installation_keys SET state = 'active', retired_at = NULL WHERE key_id = 'key-1'").run();
    }
  }
});

it('ignores legacy caps and disabled policy but rejects changed and partial transport', async () => {
  await db.prepare("UPDATE quota_caps SET cap = 1 WHERE resource = 'worker_requests'").run();
  const spy = vi.spyOn(db, 'prepare');
  try {
    expect((await handle(await request())).status).toBe(200);
  } finally { spy.mockRestore(); await db.prepare("UPDATE quota_caps SET cap = 1800 WHERE resource = 'worker_requests'").run(); }
  await db.prepare('UPDATE quota_policy SET enabled = 0').run();
  try { expect((await handle(await request())).status).toBe(200); }
  finally { await db.prepare('UPDATE quota_policy SET enabled = 1').run(); }
  for (const [header, value] of [['Content-Encoding', 'gzip'], ['Range', 'bytes=0-9'], ['If-None-Match', 'cached']]) {
    const signed = await request(), headers = new Headers(signed.headers); headers.set(header, value);
    expect((await handle(new Request(signed, { headers }))).status).toBe(400);
  }
  const signed = await request();
  expect((await handle(new Request(`${signed.url}?extra=1`, { headers: signed.headers }))).status).toBe(400);
});

it('keeps telemetry failure request-local and records denied outcomes without changing import counts', async () => {
  const before = await db.prepare("SELECT unknown_reads FROM download_usage_observations WHERE contributor_id = 'contributor-1' AND outcome = 'allowed'").first<{ unknown_reads: number }>();
  const spy = intercept('FROM artifact_payloads', async result => { result.meta.rows_read = Number.NaN; });
  try {
    const output = await contributorDownload(await request(), db, 'community.example', now, eligible, caps, signer, {});
    expect(output.status).toBe(200);
    expect(output.headers.get('Community-Usage-Status')).toBe('unavailable');
    expect(await db.prepare("SELECT unknown_reads FROM download_usage_observations WHERE contributor_id = 'contributor-1' AND outcome = 'allowed'").first())
      .toEqual({ unknown_reads: before!.unknown_reads + 1 });
  } finally { spy.mockRestore(); }
  expect((await handle(await request())).headers.get('Community-Usage-Status')).toBe('unavailable');
});

it('wires native admission and continuing bot membership before warm cached Node service downloads', async () => {
  const timestamp = Math.floor(Date.now() / 1000);
  const provider = vi.fn(async () => Response.json({ user: { id: '1554889314549243999' } }));
  vi.stubGlobal('fetch', provider);
  const limiter = { limit: vi.fn(async () => ({ success: true })) };
  const scope = new Proxy(db, {});
  const config = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: scope, PUBLIC_ORIGIN: 'https://community.example', POLICY_VERSION: 'policy-1',
    DOWNLOAD_LIMITS: JSON.stringify(caps), DOWNLOAD_RECEIPT_PRIVATE_KEY: secret, DOWNLOAD_RECEIPT_KEYS: JSON.stringify(keys),
    DISCORD_GUILD_ID: '1554889314549243986', DISCORD_BOT_TOKEN: 'synthetic-test-token'};
  async function edgeRequest() {
    const signed = await signedRead(reader, `/v1/contributions/${input.submissionId}/artifact`, timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  async function keyRequest() {
    const signed = await signedRead(reader, '/v1/receipt-keys', timestamp);
    const headers = new Headers(signed.headers); headers.set('CF-Connecting-IP', '192.0.2.1');
    return new Request(signed, { headers, });
  }
  const spy = vi.spyOn(db, 'prepare');
  try {
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(true);
    spy.mockClear();
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(200);
    expect(spy.mock.calls.some(([sql]) => sql.includes('FROM artifact_payloads'))).toBe(false);
    const history = await service.fetch(await keyRequest(), config);
    expect(history.status).toBe(200);
    expect(await history.json()).toEqual({ version: 1, service: 'https://community.example', keys });
    expect(provider).toHaveBeenCalledOnce();
    await db.prepare('UPDATE discord_membership SET checked_at = ?').bind(timestamp - 900).run();
    provider.mockImplementation(async () => { throw new Error('synthetic offline provider'); });
    expect((await service.fetch(await edgeRequest(), config)).status).toBe(503);
    expect((await service.fetch(await keyRequest(), config)).status).toBe(503);
    spy.mockClear();
    expect((await service.fetch(await edgeRequest(), { ...config, TRUSTED_ACTOR_KEY: () => null })).status).toBe(503);
    expect(spy).not.toHaveBeenCalled();
    expect((await service.fetch(await edgeRequest(), { ...config, APP_ENV: 'production' })).status).toBe(503);
    expect((await service.fetch(await edgeRequest(), { ...config, DOWNLOAD_RECEIPT_PRIVATE_KEY: undefined })).status).toBe(503);
  } finally { spy.mockRestore(); vi.unstubAllGlobals(); }
});