import type { SqlDatabase } from '../../runtime/contracts';
import type { Env } from '../../config';
import { createOAuthAdmission } from './admission';
import { discordOAuth } from './oauth';
import { observeOAuthDatabase, recordOAuthUsage } from './usage';

/** Wire trusted ingress and passive activity observations into the broker. */
export async function discordService(request: Request, env: Env, now = Date.now(),
  fetchProvider: typeof fetch = fetch): Promise<Response> {
  return admittedDiscordWork(request, env, async (database, admission) =>
    discordOAuth(request, { ...env, CATALOG_DB: database }, now, fetchProvider, admission, true), now);
}

/** Apply trusted ingress and passive observations to service work. */
export async function admittedDiscordWork(request: Request, env: Env,
  work: (db: SqlDatabase | undefined, admit: ReturnType<typeof createOAuthAdmission>) => Promise<Response>,
  now = Date.now()): Promise<Response> {
  const db = env.CATALOG_DB;
  let observed = false;
  const observer = db ? observeOAuthDatabase(db) : undefined;
  const admission = createOAuthAdmission({
    trustedActorKey: env.TRUSTED_ACTOR_KEY,
    policy: async () => {
      if (!observer) return 'unavailable';
      try { await recordOAuthUsage(observer.db, 'worker_requests', 1, now); observed = true; }
      catch { }
      return 'allowed';
    },
  });
  const response = await work(observer?.db, admission);
  if (!observed && db) response.headers.set('Community-Usage-Status', 'unavailable');
  if (observed && db) {
    try {
      const written = observer!.rowsWritten();
      if (written === null) throw new Error('usage_unavailable');
      await recordOAuthUsage(db, 'd1_writes', written, now);
    } catch {
      response.headers.set('Community-Usage-Status', 'unavailable');
    }
  }
  return response;
}