import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { Env } from '../config';
import { observeDownloadDatabase } from '../budgets/download-usage';
import { recordOAuthUsage } from '../integrations/discord/usage';

export const moderationLimits = z.strictObject({ requestBytes: z.number().int().min(1).max(8192),
  maxQueries: z.number().int().min(1).max(64) });
export const operatorFailure = (status: number, code: string) => Response.json({ error: { code } }, { status });

export async function operatorReadAdmission(env: Env, now: number,
  handler: (db: SqlDatabase, requestBytes: number) => Promise<Response>): Promise<Response> {
  if (!env.CATALOG_DB || !env.MODERATION_LIMITS) return operatorFailure(503, 'moderation_not_configured');
  let limits;
  try { limits = moderationLimits.parse(JSON.parse(env.MODERATION_LIMITS)); }
  catch { return operatorFailure(503, 'moderation_not_configured'); }
  const observer = observeDownloadDatabase(env.CATALOG_DB, limits.maxQueries), db = observer.db;
  let output: Response;
  try {
    output = await handler(db, limits.requestBytes);
  } catch (error) {
    output = operatorFailure(String(error).includes('download_query_limit') ? 413 : 503,
      String(error).includes('download_query_limit') ? 'moderation_query_limit' : 'service_unavailable');
  }
  try {
    const usage = observer.snapshot();
    await recordOAuthUsage(db, 'worker_requests', 1, now * 1000);
    if (usage.rowsWritten === null) throw new Error('usage_unavailable');
    await recordOAuthUsage(db, 'd1_writes', usage.rowsWritten + 2, now * 1000);
  } catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
  return output;
}