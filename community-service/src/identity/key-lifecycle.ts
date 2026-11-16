import type { SqlDatabase } from '../runtime/contracts';
import type { AuthenticatedContributor } from '../security/contributor-auth';

export type KeyOperationResult = {
  version: 1; kind: 'key_rotation' | 'key_revocation' | 'key_recovery'; operationId: string;
  contributorId: string; installationId: string; oldKeyId: string;
  newKeyId: string | null; committedAt: number;
};

export async function keyOperation(db: SqlDatabase, contributorId: string,
  kind: KeyOperationResult['kind'], operationId: string) {
  return db.prepare(`SELECT request_digest, state, result FROM operations
    WHERE contributor_id = ? AND kind = ? AND operation_id = ?`)
    .bind(contributorId, kind, operationId)
    .first<{ request_digest: string; state: string; result: string | null }>();
}

/** Authorize, consume replay authority and publish a key change in one SQLite transaction. */
export async function changeInstallationKey(db: SqlDatabase, identity: AuthenticatedContributor,
  input: { installationId: string; oldKeyId: string; newKey?: { keyId: string; publicKey: Uint8Array } },
  now: number): Promise<KeyOperationResult> {
  if (!identity.operationId || !Number.isSafeInteger(now) || now < 0) throw new Error('invalid_key_operation');
  const kind = input.newKey ? 'key_rotation' : 'key_revocation';
  const result: KeyOperationResult = { version: 1, kind, operationId: identity.operationId,
    contributorId: identity.contributorId, installationId: input.installationId,
    oldKeyId: input.oldKeyId, newKeyId: input.newKey?.keyId ?? null, committedAt: now };
  async function previous() {
    const row = await keyOperation(db, identity.contributorId, kind, identity.operationId!);
    if (!row) return null;
    if (row.request_digest !== identity.contentDigest || row.state !== 'completed' || !row.result) {
      throw new Error('operation_conflict');
    }
    return JSON.parse(row.result) as KeyOperationResult;
  }
  const existing = await previous();
  if (existing) return existing;
  try {
    await db.batch([
      db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id)
        VALUES (?, ?, ?, ?)`).bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
      db.prepare(`UPDATE installation_keys SET state = ?, retired_at = COALESCE(retired_at, ?)
        WHERE key_id = ? AND installation_id = ?
          AND (state = 'active' OR (? = 'key_revocation' AND state = 'retired'))
          AND (? = 'key_revocation' OR key_id = ?)
          AND EXISTS (SELECT 1 FROM installations WHERE installation_id = ? AND contributor_id = ?)`)
        .bind(input.newKey ? 'retired' : 'revoked', now, input.oldKeyId, input.installationId,
          kind, kind, identity.keyId, input.installationId, identity.contributorId),
      db.prepare(`INSERT INTO operations
        (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?, ?, ?, 'completed', ?, ?, ?)`)
        .bind(identity.contributorId, kind, identity.operationId, identity.contentDigest,
          JSON.stringify(result), now, now),
      ...(input.newKey ? [db.prepare(`INSERT INTO installation_keys
        (key_id, installation_id, algorithm, public_key, state, created_at)
        VALUES (?, ?, 'Ed25519', ?, 'active', ?)`)
        .bind(input.newKey.keyId, input.installationId, input.newKey.publicKey, now)] : []),
    ]);
    return result;
  } catch (error) {
    const raced = await previous();
    if (raced) return raced;
    if (String(error).includes('key_inactive')) throw new Error('key_inactive');
    if (String(error).includes('request_nonces')) throw new Error('nonce_conflict');
    if (error && typeof error === 'object' && 'errcode' in error &&
      typeof error.errcode === 'number' && (error.errcode & 255) === 19) throw new Error('key_operation_conflict');
    throw error;
  }
}