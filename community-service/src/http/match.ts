import type { SqlDatabase } from '../runtime/contracts';
import { recordUsage } from '../budgets/usage';
import { observeOAuthDatabase } from '../integrations/discord/usage';
import { MATCH_REQUEST_BYTES, MatchError, parseMatchRequest } from '../matching/input';
import { matchVersions, type MatchOptions } from '../matching/service';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { listAliases } from '../versions/labels';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

const headers = { 'Cache-Control': 'private, no-store' };
const failure = (status: number, code: string) => Response.json({ error: { code } }, { status, headers });

/**
 * Serve the signed read-only catalog routes `POST /v1/match` (§7.1) and `GET /v1/aliases`
 * (§7.7). Neither persists an operation or nonce.
 */
export async function contributorVersionCatalog(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: MatchOptions = {}): Promise<Response> {
  const path = new URL(request.url).pathname;
  const match = path === '/v1/match';
  if (request.method !== (match ? 'POST' : 'GET')) return failure(405, 'method_not_allowed');
  const invalid = match ? 'invalid_match' : 'invalid_request';
  if (request.url !== `https://${authority}${path}` || request.headers.has('content-encoding') ||
      (match && request.headers.get('content-type') !== 'application/json')) return failure(400, invalid);
  const observer = observeOAuthDatabase(db);
  let contributorId: string | undefined, output: Response;
  try {
    const body = await readBoundedBytes(request, MATCH_REQUEST_BYTES);
    const identity = await authenticateContributor(request, db, authority, now, membership,
      { body, maxBodyBytes: MATCH_REQUEST_BYTES });
    contributorId = identity.contributorId;
    if (!match && body.length) return failure(400, invalid);
    const result = match ? { version: 1, results: await matchVersions(db, parseMatchRequest(body), options) }
      : { version: 1, aliases: await listAliases(db) };
    output = Response.json(result, { headers });
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof MatchError) output = failure(400, error.code);
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    const outcome = output.status === 200 ? 'allowed' : 'denied';
    try {
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: now * 1000 });
      const written = observer.rowsWritten();
      if (written === null) throw new Error('usage_unavailable');
      await recordUsage(db, { contributorId, resource: 'd1_writes', outcome, units: written + 2, now: now * 1000 });
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}
