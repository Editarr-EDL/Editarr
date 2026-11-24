import type { SqlDatabase } from '../runtime/contracts';
import type { MasterPolicy } from '../filters/policy';
import type { MembershipCheck } from '../security/contributor-auth';
import { syncRevisions } from '../master/sync';
import { contributorRoute, json, jsonBody, RouteError } from './contributor-route';

/** Bounds for the read-only bulk check: 500 items fit well within this body size. */
const SYNC_REQUEST_BYTES = 131_072;
const SYNC_QUERIES = 32;

/** Serve the read-only `POST /v1/sync/revisions` (§7.5); it persists no operation and issues no receipt. */
export async function contributorSync(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: { policy: MasterPolicy }): Promise<Response> {
  return contributorRoute(request, db, { authority, now, membership, method: 'POST', invalid: 'invalid_sync',
    maxBodyBytes: SYNC_REQUEST_BYTES, maxQueries: SYNC_QUERIES }, async context => {
    let body: unknown;
    try { body = jsonBody(context.body); } catch { throw new RouteError(400, 'invalid_sync'); }
    return json(await syncRevisions(context.db, context.identity, body, options.policy, now));
  });
}
