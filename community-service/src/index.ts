import { missingConfig, type Env } from './config';
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
import { contributorPublication } from './http/publication';
import { publicationLimits } from './contributions/publication-input';
import { contributorCatalog } from './http/catalog';
import { searchLimits } from './catalog/search-input';
import { contributorDownload } from './http/download';
import { downloadLimits } from './contributions/download';
import { loadReceiptSigner, receiptKeysSchema } from './contributions/receipts';
import { contributorImport, importLimits } from './http/imports';
import { contributorVote } from './http/votes';
import { voteLimits } from './contributions/votes';
import { operatorService } from './admin/service';
import { contributorReport } from './http/reports';
import { reportLimits } from './contributions/reports';
import { RETENTION_BATCH_SIZE } from './retention/transient';

function jsonResponse(status: number, code: string): Response {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'no-store' } });
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
    if (/^\/v1\/contributions\/[^/]+\/reports$/.test(url.pathname)) {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION || !env.REPORT_LIMITS ||
          url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      let limits;
      try { limits = reportLimits.parse(JSON.parse(env.REPORT_LIMITS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorReport(request, db, new URL(env.PUBLIC_ORIGIN!).host, Math.floor(Date.now() / 1000),
          createBotMembership({ ...env, CATALOG_DB: db }), limits);
      });
    }
    if (/^\/v1\/contributions\/[^/]+\/votes?$/.test(url.pathname)) {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION || !env.VOTE_LIMITS ||
          url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      let limits;
      try { limits = voteLimits.parse(JSON.parse(env.VOTE_LIMITS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorVote(request, db, new URL(env.PUBLIC_ORIGIN!).host, Math.floor(Date.now() / 1000),
          createBotMembership({ ...env, CATALOG_DB: db }), limits);
      });
    }
    if (url.pathname === '/v1/import-receipts') {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION || !env.IMPORT_LIMITS ||
          !env.DOWNLOAD_RECEIPT_KEYS || url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      let limits, keys;
      try { limits = importLimits.parse(JSON.parse(env.IMPORT_LIMITS)); keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorImport(request, db, new URL(env.PUBLIC_ORIGIN!).host, Math.floor(Date.now() / 1000),
          createBotMembership({ ...env, CATALOG_DB: db }), limits, keys);
      });
    }
    if (/^\/v1\/contributions\/[^/]+\/artifact$/.test(url.pathname) || url.pathname === '/v1/receipt-keys') {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION || !env.DOWNLOAD_LIMITS ||
          !env.DOWNLOAD_RECEIPT_PRIVATE_KEY || !env.DOWNLOAD_RECEIPT_KEYS || url.origin !== env.PUBLIC_ORIGIN)
        return jsonResponse(503, 'service_unavailable');
      let limits;
      try { limits = downloadLimits(JSON.parse(env.DOWNLOAD_LIMITS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        let signer, keys;
        try {
          keys = receiptKeysSchema.parse(JSON.parse(env.DOWNLOAD_RECEIPT_KEYS!));
          signer = await loadReceiptSigner(env.DOWNLOAD_RECEIPT_PRIVATE_KEY!, keys);
        }
        catch { return jsonResponse(503, 'service_unavailable'); }
        return contributorDownload(request, db, new URL(env.PUBLIC_ORIGIN!).host, Math.floor(Date.now() / 1000),
          createBotMembership({ ...env, CATALOG_DB: db }), limits, signer, env.CATALOG_DB, keys);
      });
    }
    if (['/v1/availability/batch', '/v1/matches'].includes(url.pathname)) {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION ||
          !env.LOOKUP_LIMITS || url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      let limits;
      try { limits = searchLimits(JSON.parse(env.LOOKUP_LIMITS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorCatalog(request, db, new URL(env.PUBLIC_ORIGIN!).host,
          Math.floor(Date.now() / 1000), createBotMembership({ ...env, CATALOG_DB: db }), limits, env.POLICY_VERSION!, env.CATALOG_DB);
      });
    }
    if (url.pathname === '/v1/contributions' || url.pathname.startsWith('/v1/publication-operations/')) {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION ||
          !env.PUBLICATION_LIMITS || url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      let limits;
      try { limits = publicationLimits(JSON.parse(env.PUBLICATION_LIMITS)); }
      catch { return jsonResponse(503, 'service_unavailable'); }
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorPublication(request, db, new URL(env.PUBLIC_ORIGIN!).host,
          Math.floor(Date.now() / 1000), createBotMembership({ ...env, CATALOG_DB: db }), limits);
      });
    }
    if (url.pathname.startsWith('/v1/discord/')) {
      return discordService(request, env);
    }
    if (url.pathname === '/v1/enrollments/complete') return contributorEnrollment(request, env);
    if (['/v1/keys/recover', '/v1/keys/recovery-status'].includes(url.pathname)) return contributorRecovery(request, env);
    if (['/v1/keys/rotate', '/v1/keys/revoke', '/v1/keys/rotation-status'].includes(url.pathname) ||
        url.pathname.startsWith('/v1/key-operations/')) {
      if (!env.CATALOG_DB || !env.PUBLIC_ORIGIN || !env.POLICY_VERSION ||
          url.origin !== env.PUBLIC_ORIGIN) return jsonResponse(503, 'service_unavailable');
      return admittedDiscordWork(request, env, async (db, admit) => {
        const allowed = await admit(request);
        if (!db || allowed !== 'allowed') return jsonResponse(503, 'service_unavailable');
        return contributorKeys(request, db, new URL(env.PUBLIC_ORIGIN!).host,
          Math.floor(Date.now() / 1000), createBotMembership({ ...env, CATALOG_DB: db }));
      });
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