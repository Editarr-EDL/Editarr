import { z } from 'zod';
import type { Env } from '../../config';
import { operatorFailure, operatorReadAdmission } from '../admission';
import { serviceHealth } from './health';
import { runPage } from './runs';
import { serviceTrend, TREND_METRICS, TREND_PERIODS } from './trends';

export const SYSTEM_PATHS = ['/admin/v1/health', '/admin/v1/trends', '/admin/v1/maintenance/runs'];

const trendsQuery = z.strictObject({ metric: z.enum(TREND_METRICS), period: z.enum(TREND_PERIODS).default('7d') });
const runsQuery = z.strictObject({ kind: z.enum(['retention', 'recompute_all']),
  after: z.string().max(1024).regex(/^[A-Za-z0-9+/=]+$/).optional() });
const runCursor = z.tuple([z.number().int().nonnegative(), z.string().min(1).max(128)]);

class QueryError extends Error {
  constructor(public code: string) { super(code); }
}

/** Parse a strict, single-valued query string; unknown or repeated parameters are refused with `code`. */
function query<T extends z.ZodType>(url: URL, schema: T, code: string): z.infer<T> {
  const keys = [...url.searchParams.keys()];
  if (new Set(keys).size !== keys.length) throw new QueryError(code);
  const parsed = schema.safeParse(Object.fromEntries(url.searchParams));
  if (!parsed.success) throw new QueryError(code);
  return parsed.data;
}

function cursor(value: string | undefined): [number, string] | null {
  if (value === undefined) return null;
  try { return runCursor.parse(JSON.parse(atob(value))); }
  catch { throw new QueryError('invalid_maintenance_query'); }
}

/** Serve `GET /admin/v1/health`, `/admin/v1/trends` and `/admin/v1/maintenance/runs`; `now` is Unix seconds. */
export async function operatorSystem(request: Request, env: Env, now: number): Promise<Response> {
  if (request.method !== 'GET') return operatorFailure(405, 'method_not_allowed');
  const nowMs = now * 1000;
  return operatorReadAdmission(env, now, async db => {
    const url = new URL(request.url);
    try {
      if (url.pathname === '/admin/v1/health') {
        if (url.search) throw new QueryError('invalid_health_query');
        return Response.json(await serviceHealth(db, env, nowMs));
      }
      if (url.pathname === '/admin/v1/trends') {
        const input = query(url, trendsQuery, 'invalid_trends_query');
        return Response.json(await serviceTrend(db, input.metric, input.period, nowMs));
      }
      if (url.pathname === '/admin/v1/maintenance/runs') {
        const input = query(url, runsQuery, 'invalid_maintenance_query');
        return Response.json(await runPage(db, input.kind, cursor(input.after), nowMs));
      }
      return operatorFailure(404, 'not_found');
    } catch (error) {
      if (error instanceof QueryError) return operatorFailure(400, error.code);
      throw error;
    }
  });
}
