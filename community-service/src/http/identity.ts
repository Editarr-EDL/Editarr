import type { SqlDatabase } from '../runtime/contracts';
import { recordUsage } from '../budgets/usage';
import { authenticateContributor, ContributorAuthError, type MembershipCheck }
  from '../security/contributor-auth';
import { readBoundedJson } from './bounded-body';
import { contributorProfile, validatedName } from '../identity/profile';
import { renameContributor } from '../identity/rename';

function failure(status: number, code: string): Response {
  return Response.json({ error: { code } }, {
    status, headers: { 'Cache-Control': 'no-store' },
  });
}

export async function contributorIdentity(request: Request, db: SqlDatabase,
  authority: string, now: number, membership?: MembershipCheck,
  policyVersion?: string): Promise<Response> {
  if (!['GET', 'PATCH'].includes(request.method)) return failure(405, 'method_not_allowed');
  try {
    const copy = request.method === 'PATCH' ? request.clone() as Request : undefined;
    const identity = await authenticateContributor(request, db, authority, now, membership);
    const timestamp = now * 1000;
    if (copy) {
      if (!policyVersion || copy.url !== `https://${authority}/v1/identity` ||
          copy.headers.get('content-type') !== 'application/json' || copy.headers.has('content-encoding')) {
        return failure(400, 'invalid_rename');
      }
      let name: string;
      try {
        const body = await readBoundedJson(copy, 1024) as Record<string, unknown>;
        if (!body || Object.keys(body).length !== 1 || !('displayName' in body)) throw new Error('invalid');
        name = validatedName(body.displayName);
      } catch { return failure(400, 'invalid_display_name'); }
      const outcome = await renameContributor(db, identity, name, timestamp);
      const response = Response.json(await contributorProfile(db, identity.contributorId, identity.keyId,
        policyVersion, timestamp), { headers: { 'Cache-Control': 'no-store' } });
      try { await recordUsage(db, { contributorId: identity.contributorId,
        resource: 'worker_requests', outcome: outcome === 'existing' ? 'retried' : 'allowed',
        units: 1, now: timestamp }); }
      catch { response.headers.set('Community-Usage-Status', 'unavailable'); }
      return response;
    }
    await recordUsage(db, { contributorId: identity.contributorId,
      resource: 'worker_requests', outcome: 'allowed', units: 1, now: timestamp });
    if (policyVersion) return Response.json(await contributorProfile(db, identity.contributorId,
      identity.keyId, policyVersion, timestamp), { headers: { 'Cache-Control': 'no-store' } });
    return Response.json({ contributorId: identity.contributorId,
      displayName: identity.displayName },
    { headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof ContributorAuthError) {
      return failure(error.code === 'membership_unavailable' ? 503 :
        error.code === 'membership_ineligible' ? 403 : 401, error.code);
    }
    for (const code of ['display_name_taken', 'operation_conflict', 'nonce_conflict']) {
      if (String(error).includes(code)) return failure(409, code);
    }
    return failure(503, 'service_unavailable');
  }
}