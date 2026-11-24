import { z } from 'zod';
import type { SqlDatabase } from '../runtime/contracts';
import { recordUsage } from '../budgets/usage';
import { observeQueries } from '../budgets/query-observer';
import { authenticateContributor, ContributorAuthError, type AuthenticatedContributor,
  type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

const headers = { 'Cache-Control': 'private, no-store' };

/** A JSON response with the `/v1` cache policy. */
export function json(value: unknown, status = 200, extra: Record<string, string> = {}) {
  return Response.json(value, { status, headers: { ...headers, ...extra } });
}

/** `{"error":{"code":…}}` with optional contract-defined extra error fields. */
export function failure(status: number, code: string, fields: Record<string, unknown> = {}, extra: Record<string, string> = {}) {
  return json({ error: { code, ...fields } }, status, extra);
}

/** A contract error raised by a route handler and mapped directly to its response. */
export class RouteError extends Error {
  constructor(public status: number, public code: string, public fields: Record<string, unknown> = {},
    public headers: Record<string, string> = {}) { super(code); }
}

export interface RouteContext {
  db: SqlDatabase; identity: AuthenticatedContributor; body: Uint8Array; url: URL; now: number; authority: string;
}
export interface RouteOptions {
  authority: string; now: number; membership: MembershipCheck;
  method: 'GET' | 'POST';
  /** The route's `400` code for malformed requests. */
  invalid: string;
  maxBodyBytes: number;
  maxQueries: number;
  /** Allow a query string (GET only); its exact bytes are covered by the request signature. */
  query?: boolean;
}

/**
 * Run one signed contributor route (§2, §7): check the method and exact URL, read the bounded
 * body, authenticate, run `handler`, map common errors and record usage observations.
 */
export async function contributorRoute(request: Request, database: SqlDatabase, options: RouteOptions,
  handler: (context: RouteContext) => Promise<Response>): Promise<Response> {
  const url = new URL(request.url);
  if (request.method !== options.method) return failure(405, 'method_not_allowed');
  if (request.url !== `https://${options.authority}${url.pathname}${options.query ? url.search : ''}` ||
      request.headers.has('content-encoding') ||
      (options.method === 'POST' && request.headers.get('content-type') !== 'application/json'))
    return failure(400, options.invalid);
  const observer = observeQueries(database, options.maxQueries), db = observer.db;
  let contributorId: string | undefined, output: Response;
  try {
    const body = await readBoundedBytes(request, options.method === 'GET' ? 0 : options.maxBodyBytes);
    const identity = await authenticateContributor(request, db, options.authority, options.now, options.membership,
      { body, maxBodyBytes: Math.max(1, options.maxBodyBytes) });
    contributorId = identity.contributorId;
    output = await handler({ db, identity, body, url, now: options.now, authority: options.authority });
  } catch (error) {
    output = routeFailure(error, options.invalid);
  }
  if (contributorId) {
    const outcome = output.status === 200 ? 'allowed' : 'denied';
    try {
      await recordUsage(db, { contributorId, resource: 'worker_requests', outcome, units: 1, now: options.now * 1000 });
      const written = observer.snapshot().rowsWritten;
      if (written === null) throw new Error('usage_unavailable');
      await recordUsage(db, { contributorId, resource: 'd1_writes', outcome, units: written + 2, now: options.now * 1000 });
    } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  }
  return output;
}

function routeFailure(error: unknown, invalid: string): Response {
  if (error instanceof RouteError) return failure(error.status, error.code, error.fields, error.headers);
  if (error instanceof BodyLimitError) return failure(413, 'body_too_large');
  if (error instanceof ContributorAuthError) return failure(error.code === 'membership_unavailable' ? 503 :
    error.code === 'membership_ineligible' ? 403 : 401, error.code);
  if (error instanceof SyntaxError || error instanceof TypeError || error instanceof z.ZodError)
    return failure(400, invalid);
  const text = String(error);
  if (text.includes('key_inactive')) return failure(401, 'key_inactive');
  if (text.includes('operation_conflict')) return failure(409, 'operation_conflict');
  if (text.includes('request_nonces')) return failure(409, 'nonce_conflict');
  return failure(503, 'service_unavailable');
}

/** Parse a strict UTF-8 JSON body; malformed bytes become the route's `400`. */
export function jsonBody(body: Uint8Array): unknown {
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body));
}

/**
 * Admit one request in a fixed window for `scope` (for example `master:<contributor>`), or throw
 * `429 rate_limited` with `Retry-After` in seconds.
 */
export async function admitRate(db: SqlDatabase, scope: string, limit: number, windowSeconds: number, now: number) {
  const windowStart = now - (now % windowSeconds);
  const row = await db.prepare(`INSERT INTO rate_counters (scope, window_start, requests) VALUES (?, ?, 1)
    ON CONFLICT(scope, window_start) DO UPDATE SET requests = requests + 1 WHERE requests < ?
    RETURNING requests`).bind(scope, windowStart, limit).first<{ requests: number }>();
  if (!row) throw new RouteError(429, 'rate_limited', {}, { 'Retry-After': String(windowStart + windowSeconds - now) });
}
