import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { recordUsage } from '../budgets/usage';
import { availability, matches } from '../catalog/search';
import { availabilitySchema, matchesSchema, searchLimits, SearchError, type SearchLimits } from '../catalog/search-input';
import { observeOAuthDatabase } from '../integrations/discord/usage';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

function response(status: number, value: unknown) {
  return Response.json(value, { status, headers: { 'Cache-Control': 'no-store' } });
}
function failure(status: number, code: string) { return response(status, { error: { code } }); }
function parsed<T>(schema: z.ZodType<T>, body: Uint8Array): T {
  try { return schema.parse(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body))); }
  catch { throw new SearchError('invalid_lookup'); }
}

export async function contributorCatalog(request: Request, db: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck, limits: SearchLimits, policy: string, cacheScope: object = db): Promise<Response> {
  if (request.method !== 'POST') return failure(405, 'method_not_allowed');
  const path = new URL(request.url).pathname;
  const observer = observeOAuthDatabase(db);
  let contributorId: string | undefined;
  let output: Response;
  try {
    searchLimits(limits);
    if (!policy || !['/v1/availability/batch', '/v1/matches'].includes(path) || request.url !== `https://${authority}${path}` ||
        request.headers.has('content-encoding') || request.headers.get('content-type') !== 'application/json')
      return failure(400, 'invalid_lookup');
    const body = await readBoundedBytes(request, limits.requestBytes);
    const identity = await authenticateContributor(request, db, authority, now, membership, { body, maxBodyBytes: limits.requestBytes });
    contributorId = identity.contributorId;
    const visibility = await db.prepare('SELECT epoch FROM moderation_epoch WHERE singleton = 1').first<{ epoch: number }>();
    if (!visibility) throw new Error('catalog_unavailable');
    let result;
    if (path === '/v1/availability/batch') {
      const input = parsed(availabilitySchema, body);
      if (input.items.length > limits.maxItems) throw new SearchError('lookup_limit');
      result = await availability(db, input.items, policy, now, input.refresh, limits, cacheScope);
    } else {
      const input = parsed(matchesSchema, body);
      if (input.pageSize > limits.maxPageSize) throw new SearchError('lookup_limit');
      result = await matches(db, { id: 'target', evidence: input.evidence }, input.pageSize, input.cursor, policy, now, input.refresh, limits, cacheScope);
    }
    const active = await db.prepare(`SELECT 1 FROM installation_keys k JOIN installations i USING (installation_id)
      JOIN contributors c USING (contributor_id) WHERE k.key_id = ? AND k.state = 'active'
      AND c.state = 'active' AND c.contributor_id = ?
      AND (SELECT epoch FROM moderation_epoch WHERE singleton = 1) = ?`).bind(identity.keyId, contributorId, visibility.epoch).first();
    if (!active) {
      const current = await db.prepare('SELECT epoch FROM moderation_epoch WHERE singleton = 1').first<{ epoch: number }>();
      if (current?.epoch !== visibility.epoch) throw new Error('catalog_unavailable');
      throw new ContributorAuthError('key_inactive');
    }
    if (new TextEncoder().encode(JSON.stringify(result)).length > limits.responseBytes) throw new SearchError('lookup_limit');
    output = response(200, result);
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof ContributorAuthError) output = failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    else if (error instanceof SearchError) output = failure(error.code === 'lookup_limit' ? 413 : 400, error.code);
    else output = failure(503, 'service_unavailable');
  }
  if (contributorId) {
    try { await recordUsage(db, { contributorId, resource: 'worker_requests', outcome: output.status === 200 ? 'allowed' : 'denied',
      units: 1, now: now * 1000 }); }
    catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
    try {
      const written = observer.rowsWritten();
      if (written === null) throw new Error('usage_unavailable');
      await recordUsage(db, { contributorId, resource: 'd1_writes', outcome: output.status === 200 ? 'allowed' : 'denied',
        units: written + 2, now: now * 1000 });
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}