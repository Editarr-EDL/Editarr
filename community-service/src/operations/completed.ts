import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { AuthenticatedContributor } from '../security/contributor-auth';

/** A completed contributor operation of `kind`, or null. */
export async function completedOperation(db: SqlDatabase, identity: AuthenticatedContributor, kind: string) {
  return db.prepare(`SELECT request_digest, result FROM operations WHERE contributor_id = ? AND kind = ?
    AND operation_id = ? AND state = 'completed'`).bind(identity.contributorId, kind, identity.operationId)
    .first<{ request_digest: string; result: string }>();
}

/**
 * Return the stored result when this operation already completed with `digest`, throw
 * `operation_conflict` when it completed with different bytes, and return null otherwise.
 */
export async function replayOperation<T>(db: SqlDatabase, identity: AuthenticatedContributor, kind: string, digest: string): Promise<T | null> {
  const row = await completedOperation(db, identity, kind);
  if (!row) return null;
  if (row.request_digest !== digest) throw new Error('operation_conflict');
  return JSON.parse(row.result) as T;
}

/** The nonce reservation and completed operation that commit atomically with a mutation (§2). */
export function completionStatements(db: SqlDatabase, identity: AuthenticatedContributor, kind: string, digest: string,
  result: unknown, now: number): SqlStatement[] {
  return [
    db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
      .bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
    db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)`).bind(identity.contributorId, kind, identity.operationId, digest,
      JSON.stringify(result), now * 1000, now * 1000),
  ];
}
