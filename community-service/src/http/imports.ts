import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { recordUsage } from '../budgets/usage';
import { observeDownloadDatabase, recordImportUsage } from '../budgets/download-usage';
import { acknowledgeImport } from '../contributions/imports';
import type { ReceiptKey } from '../contributions/receipts';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

export const importLimits = z.strictObject({ requestBytes: z.number().int().min(1).max(8192),
  maxQueries: z.number().int().min(1).max(64) });
const input = z.strictObject({ version: z.literal(1), receipt: z.unknown() });
function failure(status: number, code: string) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'private, no-store' } });
}

export async function contributorImport(request: Request, database: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck, limits: z.infer<typeof importLimits>, keys: ReceiptKey[]): Promise<Response> {
  if (request.method !== 'POST') return failure(405, 'method_not_allowed');
  if (request.url !== `https://${authority}/v1/import-receipts` || request.headers.has('content-encoding') ||
      request.headers.get('content-type') !== 'application/json') return failure(400, 'invalid_import_receipt');
  const observer = observeDownloadDatabase(database, limits.maxQueries), db = observer.db;
  let contributorId: string | undefined, output: Response;
  try {
    importLimits.parse(limits);
    const body = await readBoundedBytes(request, limits.requestBytes);
    const identity = await authenticateContributor(request, db, authority, now, membership, { body, maxBodyBytes: limits.requestBytes });
    contributorId = identity.contributorId;
    const result = await acknowledgeImport(db, identity, `https://${authority}`,
      () => input.parse(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body))).receipt, keys, now);
    output = Response.json({ version: 1, state: 'completed', result }, { headers: { 'Cache-Control': 'private, no-store' } });
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof z.ZodError || error instanceof SyntaxError || error instanceof TypeError) output = failure(400, 'invalid_import_receipt');
    else if (String(error).includes('key_inactive')) output = failure(401, 'key_inactive');
    else if (String(error).includes('operation_conflict')) output = failure(409, 'operation_conflict');
    else if (String(error).includes('request_nonces')) output = failure(409, 'nonce_conflict');
    else if (String(error).includes('invalid_download_receipt') || String(error).includes('invalid_import_receipt')) output = failure(400, 'invalid_import_receipt');
    else if (String(error).includes('download_query_limit')) output = failure(413, 'import_query_limit');
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    try {
      const outcome = output.status === 200 ? 'allowed' : 'denied';
      const usage = observer.snapshot();
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: now * 1000 });
      if (usage.rowsWritten !== null) await recordUsage(db, { contributorId, resource: 'd1_writes', outcome,
        units: usage.rowsWritten + 2, now: now * 1000 });
      await recordImportUsage(db, contributorId, outcome, now, observer.snapshot());
      if (usage.rowsRead === null || usage.rowsWritten === null) output.headers.set('Community-Usage-Status', 'unavailable');
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}