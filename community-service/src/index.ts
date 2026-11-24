import { masterPolicy, missingConfig, routeLimits, voteLimits, type Env } from './config';
import { contributorIdentity } from './http/identity';
import { contributorEnrollment } from './http/enrollment';
import { admittedDiscordWork, discordService } from './integrations/discord/service';
import { pruneDiscordOAuth } from './integrations/discord/oauth';
import { observeOAuthDatabase, pruneOAuthUsage, recordOAuthUsage } from './integrations/discord/usage';
import { pruneExpiredAuthenticationState } from './operations/repository';
import { createBotMembership, pruneBotMembership } from './integrations/discord/membership';
import { pruneProfileNames } from './identity/rename';
import { contributorKeys } from './http/key-lifecycle';
import { contributorRecovery } from './http/recovery';
import { pruneRecoveryAuthorizations } from './identity/recovery';
import { contributorVersionCatalog } from './http/match';
import { operatorService } from './admin/service';
import { RETENTION_BATCH_SIZE } from './retention/transient';
import { pruneRateWindows, pruneSnapshotBytes } from './retention/windows';
import { loadReceiptSigner, receiptKeysSchema } from './security/service-signer';
import { contributorMasterRead } from './http/master';
import { contributorSubmission } from './http/submissions';
import { submissionLimitsSchema } from './submissions/input';
import { servedFilterCounts } from './master/reads';
import { contributorVotes } from './http/votes';
import { contributorSync } from './http/sync';
import { contributorReport } from './http/reports';
import type { SqlDatabase } from './runtime/contracts';
import type { MembershipCheck } from './security/contributor-auth';

function jsonResponse(status: number, code: string): Response {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

/**
 * Run a signed `/v1` route behind edge admission with the shared configuration checks: a
 * database, origin and policy version are required, and requests must target the public origin.
 */
function contributorWork(request: Request, env: Env,
  handler: (db: SqlDatabase, authority: string, now: number, membership: MembershipCheck) => Promise<Response>) {
  if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION || new URL(request.url).origin !== env.PUBLIC_ORIGIN)
    return jsonResponse(503, 'service_unavailable');
  return admittedDiscordWork(request, env, async (db, admit) => {
    const allowed = await admit(request);
    if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
    return handler(db, new URL(env.PUBLIC_ORIGIN!).host, Math.floor(Date.now() / 1000), createBotMembership({ ...env, CATALOG_DB: db }));
  });
}

export default {
  fetch(request: Request, env: Env): Response | Promise<Response> {
    const url = new URL(request.url);
    if (env.APP_ENV !== 'local' && !(env.APP_ENV === 'self-hosted' && env.SELF_HOSTED_ENABLED === true)) {
      return jsonResponse(503, 'service_unavailable');
    }
    if (url.pathname === '/admin' || url.pathname.startsWith('/admin/')) {
      return operatorService(request, env);
    }
    if (url.pathname === '/v1/receipt-keys') {
      if (!env.DOWNLOAD_RECEIPT_KEYS) return jsonResponse(503, 'service_unavailable');
      let keys;
      try { keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorMasterRead(request, db, authority, now, membership, { receiptKeys: keys }));
    }
    if (/^\/v1\/versions\/[^/]+\/master$/.test(url.pathname)) {
      return contributorWork(request, env, async (db, authority, now, membership) => {
        let keys, signer, policy;
        try {
          policy = masterPolicy(env);
          keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS ?? ''));
          signer = await loadReceiptSigner(env.DOWNLOAD_RECEIPT_PRIVATE_KEY ?? '', keys);
        } catch { return jsonResponse(503, 'service_unavailable'); }
        return contributorMasterRead(request, db, authority, now, membership,
          { receiptKeys: keys, signer, versionChecksPerHour: policy.versionChecksPerHour });
      });
    }
    if (url.pathname === '/v1/match' || url.pathname === '/v1/aliases') {
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorVersionCatalog(request, db, authority, now, membership, { filterCounts: servedFilterCounts }));
    }
    if (/^\/v1\/filters\/[^/]+\/votes$/.test(url.pathname) || /^\/v1\/versions\/[^/]+\/votes$/.test(url.pathname)) {
      let policy, limits;
      try { policy = masterPolicy(env); limits = voteLimits.parse(JSON.parse(env.VOTE_LIMITS ?? '')); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorVotes(request, db, authority, now, membership, { policy, limits }));
    }
    if (url.pathname === '/v1/sync/revisions') {
      let policy;
      try { policy = masterPolicy(env); } catch { return jsonResponse(503, 'service_unavailable'); }
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorSync(request, db, authority, now, membership, { policy }));
    }
    if (url.pathname === '/v1/reports') {
      let limits;
      try { limits = routeLimits.parse(JSON.parse(env.REPORT_LIMITS ?? '')); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorReport(request, db, authority, now, membership, limits));
    }
    if (url.pathname === '/v1/submissions') {
      let policy, limits;
      try { policy = masterPolicy(env); limits = submissionLimitsSchema.parse(JSON.parse(env.SUBMISSION_LIMITS ?? '')); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return contributorWork(request, env, (db, authority, now, membership) =>
        contributorSubmission(request, db, authority, now, membership, { policy, limits }));
    }
    if (url.pathname.startsWith('/v1/discord/')) {
      return discordService(request, env);
    }
    if (url.pathname === '/v1/enrollments/complete') return contributorEnrollment(request, env);
    if (['/v1/keys/recover', '/v1/keys/recovery-status'].includes(url.pathname)) return contributorRecovery(request, env);
    if (['/v1/keys/rotate', '/v1/keys/revoke', '/v1/keys/rotation-status'].includes(url.pathname) ||
        url.pathname.startsWith('/v1/key-operations/')) {
      return contributorWork(request, env, (db, authority, now, membership) => contributorKeys(request, db, authority, now, membership));
    }
    if (url.pathname === '/v1/identity') {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION ||
          url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      if (request.method === 'PATCH') {
        return admittedDiscordWork(request, env, async (db, admit) => {
          const allowed = await admit(request);
          if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
          return contributorIdentity(request, db, new URL(env.PUBLIC_ORIGIN!).host,
            Math.floor(Date.now() / 1000), createBotMembership({ ...env, CATALOG_DB: db }), env.POLICY_VERSION);
        });
      }
      return contributorIdentity(request, env.CATALOG_DB, new URL(env.PUBLIC_ORIGIN).host,
        Math.floor(Date.now() / 1000), createBotMembership(env), env.POLICY_VERSION);
    }
    if (request.method !== 'GET') return jsonResponse(404, 'not_found');
    if (url.pathname === '/health') return Response.json({ status: 'ok' });
    if (url.pathname === '/v1/capabilities') {
      if (missingConfig(env).length) return jsonResponse(503, 'service_unavailable');
      return Response.json({ status: 'unavailable' }, { status: 503 });
    }
    return jsonResponse(404, 'not_found');
  },
  async scheduled(_event: unknown, env: Env): Promise<void> {
    if ((env.APP_ENV !== 'local' && !(env.APP_ENV === 'self-hosted' && env.SELF_HOSTED_ENABLED === true)) || !env.CATALOG_DB) return;
    const now = Date.now();
    const observer = observeOAuthDatabase(env.CATALOG_DB);
    const db = observer.db, seconds = Math.floor(now / 1000);
    // Each prune runs even if an earlier one fails; failures are rethrown after usage is recorded.
    const errors: unknown[] = [];
    for (const prune of [
      () => pruneDiscordOAuth(db, now, RETENTION_BATCH_SIZE),
      () => pruneRecoveryAuthorizations(db, now, RETENTION_BATCH_SIZE),
      () => pruneExpiredAuthenticationState(db, seconds, RETENTION_BATCH_SIZE),
      () => pruneOAuthUsage(db, now, RETENTION_BATCH_SIZE),
      () => pruneBotMembership(db, seconds, RETENTION_BATCH_SIZE),
      () => pruneProfileNames(db, now, RETENTION_BATCH_SIZE),
      () => pruneRateWindows(db, now, RETENTION_BATCH_SIZE),
      () => pruneSnapshotBytes(db, now, RETENTION_BATCH_SIZE),
    ]) {
      try { await prune(); } catch (error) { errors.push(error); }
    }
    const written = observer.rowsWritten();
    if (written === null) errors.push(new Error('usage_unavailable'));
    else {
      try { await recordOAuthUsage(env.CATALOG_DB, 'd1_writes', written, now); }
      catch (error) { errors.push(error); }
    }
    if (errors.length === 1) throw errors[0];
    if (errors.length > 1) throw new AggregateError(errors, 'retention_prune_failed');
  },
};