import type { SqlDatabase } from '../runtime/contracts';
import { recordUsage } from '../budgets/usage';
import { observeDownloadDatabase, recordDownloadUsage } from '../budgets/download-usage';
import { downloadArtifact, downloadLimits, DownloadError, type DownloadLimits } from '../contributions/download';
import { base64, receiptKeysSchema, type ReceiptKey, type ReceiptSigner } from '../contributions/receipts';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

function failure(status: number, code: string) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'private, no-store, no-transform' } });
}

export async function contributorDownload(request: Request, database: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck, limits: DownloadLimits, signer: ReceiptSigner, cacheScope: object = database,
  receiptKeys?: ReceiptKey[]): Promise<Response> {
  if (request.method !== 'GET') return failure(405, 'method_not_allowed');
  const path = new URL(request.url).pathname;
  const route = /^\/v1\/contributions\/([A-Za-z0-9_.-]{1,128})\/artifact$/.exec(path);
  const keyHistory = path === '/v1/receipt-keys';
  if ((!route && !keyHistory) || request.url !== `https://${authority}${path}` || request.headers.has('content-encoding') ||
      ['range', 'if-none-match', 'if-modified-since'].some(header => request.headers.has(header))) return failure(400, 'invalid_download');
  const observer = observeDownloadDatabase(database, limits.maxQueries);
  const db = observer.db;
  let contributorId: string | undefined, output: Response;
  try {
    downloadLimits(limits);
    const body = await readBoundedBytes(request, 0);
    const identity = await authenticateContributor(request, db, authority, now, membership, { body, maxBodyBytes: limits.requestBytes });
    contributorId = identity.contributorId;
    const value = keyHistory
      ? { version: 1, service: `https://${authority}`, keys: receiptKeysSchema.parse(receiptKeys) }
      : await downloadArtifact(db, identity, route![1], `https://${authority}`, now, limits, signer, cacheScope);
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    if (bytes.length > limits.responseBytes) throw new DownloadError('download_limit');
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
    output = new Response(bytes, { headers: { 'Content-Type': 'application/json', 'Content-Length': String(bytes.length),
      'Content-Digest': `sha-256=:${base64(digest)}:`, 'Cache-Control': 'private, no-store, no-transform' } });
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof DownloadError) output = failure(error.code === 'key_inactive' ? 401 :
      error.code === 'contribution_not_found' ? 404 : error.code === 'download_limit' ? 413 :
      ['contribution_unavailable', 'historical_key_untrusted'].includes(error.code) ? 410 : 503, error.code);
    else if (String(error).includes('download_query_limit')) output = failure(413, 'download_query_limit');
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    const outcome = output.status === 200 ? 'allowed' : 'denied';
    try {
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: now * 1000 });
      const written = observer.snapshot().rowsWritten;
      if (written !== null) await recordUsage(db, { contributorId, resource: 'd1_writes', outcome, units: written + 2, now: now * 1000 });
      const usage = observer.snapshot();
      await recordDownloadUsage(db, contributorId, outcome, now, usage);
      if (usage.rowsRead === null || usage.rowsWritten === null) output.headers.set('Community-Usage-Status', 'unavailable');
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}