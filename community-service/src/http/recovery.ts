import type { Env } from '../config';
import { recordUsage } from '../budgets/usage';
import { recoverInstallation } from '../identity/recovery';
import { createBotMembership } from '../integrations/discord/membership';
import { admittedDiscordWork } from '../integrations/discord/service';
import { ZodError } from 'zod';
import { BodyLimitError } from './bounded-body';
import { RequestSignatureError, RequestSignatureTimeError } from '../security/request-signatures';

export async function contributorRecovery(request: Request, env: Env, now = Date.now(), client: typeof fetch = fetch) {
  const failure = (status: number, code: string) => Response.json({ error: { code } },
    { status, headers: { 'Cache-Control': 'no-store' } });
  if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION) return failure(503, 'service_unavailable');
  return admittedDiscordWork(request, env, async (db, admit) => {
    const admission = await admit(request);
    if (!db || admission !== 'allowed') return failure(503, 'service_unavailable');
    try {
      let contributorId = '';
      const result = await recoverInstallation(request, db, new URL(env.PUBLIC_ORIGIN!).host, now, async (subject, owner) => {
        contributorId = owner;
        const outcome = await createBotMembership({ ...env, CATALOG_DB: db }, client, undefined, subject)('', Math.floor(now / 1000));
        if (outcome !== 'eligible') throw new Error(`membership_${outcome}`);
      });
      const response = Response.json(result, { headers: { 'Cache-Control': 'no-store' } });
      try { await recordUsage(db, { contributorId,
        resource: 'worker_requests', outcome: 'allowed', units: 1, now }); }
      catch { response.headers.set('Community-Usage-Status', 'unavailable'); }
      return response;
    } catch (error) {
      if (error instanceof BodyLimitError) return failure(413, 'body_too_large');
      if (error instanceof RequestSignatureTimeError) return failure(401, 'clock_skew');
      if (error instanceof RequestSignatureError) return failure(401, 'invalid_signature');
      if (error instanceof ZodError || error instanceof SyntaxError || error instanceof TypeError) return failure(400, 'invalid_recovery');
      const message = String(error);
      for (const [code, status] of [['membership_unavailable', 503], ['membership_ineligible', 403],
        ['recovery_expired', 409], ['recovery_conflict', 409], ['operation_conflict', 409],
        ['key_inactive', 401]] as const) if (message.includes(code)) return failure(status, code);
      if (message.includes('invalid_recovery')) return failure(400, 'invalid_recovery');
      return failure(503, 'service_unavailable');
    }
  }, now);
}