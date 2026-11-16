import type { SqlDatabase } from '../runtime/contracts';
import { recordUsage } from '../budgets/usage';
import { changeInstallationKey, keyOperation, type KeyOperationResult } from '../identity/key-lifecycle';
import { publicBytes, rotationIntent, verifyRotationProofs } from '../identity/rotation-proof';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { verifyRequestSignature, RequestSignatureTimeError } from '../security/request-signatures';
import { BodyLimitError, readBoundedJson } from './bounded-body';

function failure(status: number, code: string) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

function success(value: unknown) {
  return Response.json(value, { headers: { 'Cache-Control': 'no-store' } });
}

async function membershipRequired(check: MembershipCheck, contributorId: string, now: number) {
  const outcome = await check(contributorId, now);
  if (outcome !== 'eligible') throw new ContributorAuthError(outcome === 'ineligible'
    ? 'membership_ineligible' : 'membership_unavailable');
}

async function report(db: SqlDatabase, contributorId: string, now: number, response: Response) {
  try { await recordUsage(db, { contributorId, resource: 'worker_requests', outcome: 'allowed', units: 1, now }); }
  catch { response.headers.set('Community-Usage-Status', 'unavailable'); }
  return response;
}

/** Serve only key lifecycle operations; inactive keys never gain general request authority. */
export async function contributorKeys(request: Request, db: SqlDatabase, authority: string,
  now: number, membership: MembershipCheck): Promise<Response> {
  const path = new URL(request.url).pathname;
  const status = path === '/v1/keys/rotation-status';
  const operationMatch = /^\/v1\/key-operations\/(key_rotation|key_revocation|key_recovery)\/([A-Za-z0-9._~-]{1,128})$/.exec(path);
  if (operationMatch ? request.method !== 'GET' : request.method !== 'POST') {
    return failure(405, 'method_not_allowed');
  }
  try {
    if (operationMatch) {
      const identity = await authenticateContributor(request, db, authority, now, membership);
      const row = await keyOperation(db, identity.contributorId,
        operationMatch[1] as KeyOperationResult['kind'], operationMatch[2]);
      if (!row || !row.result) return failure(404, 'operation_not_found');
      const active = await db.prepare(`SELECT 1 FROM installation_keys AS key
        JOIN installations AS installation ON installation.installation_id = key.installation_id
        JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
        WHERE key.key_id = ? AND key.state = 'active' AND contributor.state = 'active'
          AND contributor.contributor_id = ?`).bind(identity.keyId, identity.contributorId).first();
      if (!active) throw new ContributorAuthError('key_inactive');
      return report(db, identity.contributorId, now * 1000, success({ state: row.state, result: JSON.parse(row.result) }));
    }
    if (!['/v1/keys/rotate', '/v1/keys/revoke', '/v1/keys/rotation-status'].includes(path) ||
      request.url !== `https://${authority}${path}` || request.headers.get('content-type') !== 'application/json' ||
      request.headers.has('content-encoding')) return failure(400, 'invalid_key_operation');
    const copy = request.clone() as Request;
    if (status) {
      const body = rotationIntent(await readBoundedJson(copy, 2048));
      const old = await db.prepare(`SELECT key.public_key, key.state, installation.contributor_id
        FROM installation_keys AS key JOIN installations AS installation ON installation.installation_id = key.installation_id
        JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
        WHERE key.key_id = ? AND key.installation_id = ? AND contributor.state = 'active'`)
        .bind(body.oldKeyId, body.installationId).first<{ public_key: ArrayBuffer; state: string; contributor_id: string }>();
      if (!old) return failure(401, 'invalid_rotation_proof');
      const operationId = request.headers.get('idempotency-key');
      if (!operationId || !/^[A-Za-z0-9._~-]{1,128}$/.test(operationId)) return failure(400, 'invalid_key_operation');
      const replacement = await verifyRotationProofs(body, new Uint8Array(old.public_key), authority, operationId);
      try {
        await verifyRequestSignature({ method: request.method, url: request.url, headers: request.headers,
          body: new Uint8Array(await request.arrayBuffer()) }, replacement.publicKey, {
          expectedAuthority: authority, expectedKeyId: replacement.keyId, now, maxLifetimeSeconds: 120, maxClockSkewSeconds: 5,
        });
      } catch (error) { return failure(401, error instanceof RequestSignatureTimeError ? 'clock_skew' : 'invalid_signature'); }
      await membershipRequired(membership, old.contributor_id, now);
      const row = await keyOperation(db, old.contributor_id, 'key_rotation', operationId);
      const result = row?.result ? JSON.parse(row.result) as KeyOperationResult : null;
      const digest = request.headers.get('content-digest');
      if (row && (row.request_digest !== digest || row.state !== 'completed' || !result ||
          result.installationId !== body.installationId || result.oldKeyId !== body.oldKeyId ||
          result.newKeyId !== replacement.keyId)) return failure(409, 'operation_conflict');
      const current = await db.prepare(`SELECT key.key_id, key.state FROM installation_keys AS key
        JOIN installations AS installation ON installation.installation_id = key.installation_id
        JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
        WHERE installation.installation_id = ? AND contributor.contributor_id = ? AND contributor.state = 'active'
          AND key.key_id = ? AND key.state = 'active'`)
        .bind(body.installationId, old.contributor_id, result ? replacement.keyId : body.oldKeyId).first();
      if (!current) throw new ContributorAuthError('key_inactive');
      return report(db, old.contributor_id, now * 1000, success({ version: 1,
        state: result ? 'completed' : 'not_committed', result }));
    }
    const identity = await authenticateContributor(request, db, authority, now, membership);
    const value = await readBoundedJson(copy, 2048);
    let input: Parameters<typeof changeInstallationKey>[2];
    if (path === '/v1/keys/rotate') {
      const body = rotationIntent(value);
      if (identity.keyId !== body.oldKeyId || !identity.operationId) return failure(401, 'invalid_rotation_proof');
      const old = await db.prepare('SELECT public_key FROM installation_keys WHERE key_id = ?')
        .bind(identity.keyId).first<{ public_key: ArrayBuffer }>();
      if (!old) throw new ContributorAuthError('key_inactive');
      input = { installationId: body.installationId, oldKeyId: body.oldKeyId,
        newKey: await verifyRotationProofs(body, new Uint8Array(old.public_key), authority, identity.operationId) };
    } else {
      const body = value as Record<string, unknown> | null;
      if (!body || Array.isArray(body) || Object.keys(body).sort().join(',') !== 'installationId,keyId,version' ||
        body.version !== 1 || typeof body.installationId !== 'string' ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(body.installationId) || typeof body.keyId !== 'string' ||
        !/^sha256:[0-9a-f]{64}$/.test(body.keyId)) return failure(400, 'invalid_key_operation');
      input = { installationId: body.installationId, oldKeyId: body.keyId };
    }
    const result = await changeInstallationKey(db, identity, input, now * 1000);
    return report(db, identity.contributorId, now * 1000, success({ version: 1, state: 'completed', result }));
  } catch (error) {
    if (error instanceof BodyLimitError) return failure(413, 'body_too_large');
    if (error instanceof SyntaxError || error instanceof TypeError) return failure(400, 'invalid_key_operation');
    if (error instanceof ContributorAuthError) return failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    const message = String(error);
    if (message.includes('key_inactive')) return failure(401, 'key_inactive');
    if (message.includes('invalid_rotation_proof')) return failure(401, 'invalid_rotation_proof');
    if (message.includes('invalid_key_operation') || message.includes('invalid_body')) return failure(400, 'invalid_key_operation');
    for (const code of ['operation_conflict', 'key_operation_conflict', 'nonce_conflict']) {
      if (message.includes(code)) return failure(409, code);
    }
    return failure(503, 'service_unavailable');
  }
}