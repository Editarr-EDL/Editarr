import type { SqlDatabase } from '../runtime/contracts';
import type { RouteLimits } from '../config';
import type { MasterPolicy } from '../filters/policy';
import type { MembershipCheck } from '../security/contributor-auth';
import { castVote, ownVotes } from '../master/votes';
import { contributorRoute, failure, json, jsonBody, RouteError } from './contributor-route';

const FILTER_VOTE = /^\/v1\/filters\/([^/]+)\/votes$/;
const VERSION_VOTES = /^\/v1\/versions\/([^/]+)\/votes$/;
const TOKEN = /^[A-Za-z0-9_.-]{1,128}$/;
/** Recompute after a vote reads every input of its version; the configured bound covers the rest. */
const RECOMPUTE_QUERIES = 40;

/** Serve `POST /v1/filters/{filterId}/votes` and `GET /v1/versions/{versionId}/votes` (§7.4). */
export async function contributorVotes(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: { policy: MasterPolicy; limits: RouteLimits }): Promise<Response> {
  const path = new URL(request.url).pathname, cast = FILTER_VOTE.exec(path), list = VERSION_VOTES.exec(path);
  if (!cast && !list) return failure(404, 'not_found');
  return contributorRoute(request, db, { authority, now, membership, method: cast ? 'POST' : 'GET',
    invalid: cast ? 'invalid_vote' : 'invalid_request', maxBodyBytes: cast ? options.limits.requestBytes : 0,
    maxQueries: options.limits.maxQueries + RECOMPUTE_QUERIES }, async context => {
    const id = (cast ?? list)![1];
    if (!TOKEN.test(id)) throw new RouteError(400, cast ? 'invalid_vote' : 'invalid_request');
    if (list) return json(await ownVotes(context.db, context.identity, id));
    let body: unknown;
    try { body = jsonBody(context.body); } catch { throw new RouteError(400, 'invalid_vote'); }
    const result = await castVote(context.db, context.identity, id, body, { service: `https://${authority}`, policy: options.policy, now });
    return json({ version: 1, state: 'completed', result });
  });
}
