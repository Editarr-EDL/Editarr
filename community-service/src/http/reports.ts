import type { SqlDatabase } from '../runtime/contracts';
import type { RouteLimits } from '../config';
import type { MembershipCheck } from '../security/contributor-auth';
import { fileReport } from '../master/reports';
import { contributorRoute, json, jsonBody, RouteError } from './contributor-route';

/** Serve `POST /v1/reports` (§7.6). */
export async function contributorReport(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, limits: RouteLimits): Promise<Response> {
  return contributorRoute(request, db, { authority, now, membership, method: 'POST', invalid: 'invalid_report',
    maxBodyBytes: limits.requestBytes, maxQueries: limits.maxQueries }, async context => {
    let body: unknown;
    try { body = jsonBody(context.body); } catch { throw new RouteError(400, 'invalid_report'); }
    return json({ version: 1, state: 'completed', result: await fileReport(context.db, context.identity, body, now) });
  });
}
