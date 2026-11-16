import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { recordUsage } from '../budgets/usage';
import { observeDownloadDatabase, recordVoteUsage } from '../budgets/download-usage';
import { readVote, setVote, voteLimits } from '../contributions/votes';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

function failure(status: number, code: string) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'private, no-store' } });
}

export async function contributorVote(request: Request, database: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck, limits: z.infer<typeof voteLimits>): Promise<Response> {
  const match = new URL(request.url).pathname.match(/^\/v1\/contributions\/([A-Za-z0-9_.-]{1,128})\/(vote|votes)$/);
  if (!match || request.url !== `https://${authority}${match[0]}` || request.headers.has('content-encoding'))
    return failure(400, 'invalid_vote');
  if (!['GET', 'POST'].includes(request.method) || (request.method === 'POST' && match[2] !== 'vote'))
    return failure(405, 'method_not_allowed');
  if (request.method === 'POST' && request.headers.get('content-type') !== 'application/json') return failure(400, 'invalid_vote');
  const observer = observeDownloadDatabase(database, limits.maxQueries), db = observer.db;
  let contributorId: string | undefined, output: Response;
  try {
    voteLimits.parse(limits);
    const body = await readBoundedBytes(request, limits.requestBytes);
    const identity = await authenticateContributor(request, db, authority, now, membership, { body, maxBodyBytes: limits.requestBytes });
    contributorId = identity.contributorId;
    if (request.method === 'GET' && body.length) throw new Error('invalid_vote');
    const result = request.method === 'POST' ? { version: 1, state: 'completed',
      result: await setVote(db, identity, match[1], () => JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)), now),
    } : await readVote(db, identity, match[1], match[2] === 'vote', now);
    output = Response.json(result, { headers: { 'Cache-Control': 'private, no-store' } });
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof z.ZodError || error instanceof SyntaxError || error instanceof TypeError || String(error).includes('invalid_vote'))
      output = failure(400, 'invalid_vote');
    else if (String(error).includes('key_inactive')) output = failure(401, 'key_inactive');
    else if (String(error).includes('vote_ineligible')) output = failure(403, 'vote_ineligible');
    else if (String(error).includes('revision_not_available')) output = failure(404, 'revision_not_available');
    else if (String(error).includes('operation_conflict')) output = failure(409, 'operation_conflict');
    else if (String(error).includes('request_nonces')) output = failure(409, 'nonce_conflict');
    else if (String(error).includes('download_query_limit')) output = failure(413, 'vote_query_limit');
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    try {
      const outcome = output.status === 200 ? 'allowed' : 'denied';
      const usage = observer.snapshot();
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: now * 1000 });
      if (usage.rowsWritten !== null) await recordUsage(db, { contributorId, resource: 'd1_writes', outcome,
        units: usage.rowsWritten + 2, now: now * 1000 });
      await recordVoteUsage(db, contributorId, outcome, now, observer.snapshot());
      if (usage.rowsRead === null || usage.rowsWritten === null) output.headers.set('Community-Usage-Status', 'unavailable');
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}