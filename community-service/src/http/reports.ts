import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { recordUsage } from '../budgets/usage';
import { observeDownloadDatabase, recordReportUsage } from '../budgets/download-usage';
import { submitReport, reportLimits, type ReportLimits } from '../contributions/reports';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

const failure = (status: number, code: string) => Response.json({ error: { code } },
  { status, headers: { 'Cache-Control': 'private, no-store' } });

export async function contributorReport(request: Request, database: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck, limits: ReportLimits): Promise<Response> {
  const match = new URL(request.url).pathname.match(/^\/v1\/contributions\/([A-Za-z0-9_.-]{1,128})\/reports$/);
  if (!match || request.url !== `https://${authority}${match[0]}` || request.headers.has('content-encoding')) return failure(400, 'invalid_report');
  if (request.method !== 'POST') return failure(405, 'method_not_allowed');
  if (request.headers.get('content-type') !== 'application/json') return failure(400, 'invalid_report');
  const observer = observeDownloadDatabase(database, limits.maxQueries), db = observer.db;
  let contributorId: string | undefined, output: Response;
  try {
    reportLimits.parse(limits);
    const body = await readBoundedBytes(request, limits.requestBytes);
    const identity = await authenticateContributor(request, db, authority, now, membership, { body, maxBodyBytes: limits.requestBytes });
    contributorId = identity.contributorId;
    const result = await submitReport(db, identity, match[1], () => JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)), now, limits);
    output = Response.json({ version: 1, state: 'completed', result }, { headers: { 'Cache-Control': 'private, no-store' } });
  } catch (error) {
    const text = String(error);
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 : error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof z.ZodError || error instanceof SyntaxError || error instanceof TypeError || text.includes('invalid_report')) output = failure(400, 'invalid_report');
    else if (text.includes('key_inactive')) output = failure(401, 'key_inactive');
    else if (text.includes('report_ineligible')) output = failure(403, 'report_ineligible');
    else if (text.includes('revision_not_available')) output = failure(404, 'revision_not_available');
    else if (text.includes('report_already_open')) output = failure(409, 'report_already_open');
    else if (text.includes('operation_conflict')) output = failure(409, 'operation_conflict');
    else if (text.includes('request_nonces')) output = failure(409, 'nonce_conflict');
    else if (text.includes('download_query_limit')) output = failure(413, 'report_query_limit');
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    try {
      const outcome = output.status === 200 ? 'allowed' : 'denied', usage = observer.snapshot();
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: now * 1000 });
      if (usage.rowsWritten !== null) await recordUsage(db, { contributorId, resource: 'd1_writes', outcome, units: usage.rowsWritten + 2, now: now * 1000 });
      await recordReportUsage(db, contributorId, outcome, now, observer.snapshot());
      if (usage.rowsRead === null || usage.rowsWritten === null) output.headers.set('Community-Usage-Status', 'unavailable');
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}