import type { SqlDatabase } from '../runtime/contracts';
import { transientStatement } from '../retention/transient';
export type Operation = {
  contributorId: string;
  kind: string;
  operationId: string;
  requestDigest: string;
  now: number;
};

export async function reserveOperation(db: SqlDatabase, input: Operation): Promise<'created' | 'existing'> {
  const result = await db.prepare(`INSERT INTO operations
    (contributor_id, kind, operation_id, request_digest, state, created_at, updated_at)
    VALUES (?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT(contributor_id, kind, operation_id) DO NOTHING`)
    .bind(input.contributorId, input.kind, input.operationId, input.requestDigest,
      input.now, input.now).run();
  if (result.meta.changes === 1) return 'created';
  const existing = await db.prepare(`SELECT request_digest FROM operations
    WHERE contributor_id = ? AND kind = ? AND operation_id = ?`).bind(
    input.contributorId, input.kind, input.operationId,
  ).first<{ request_digest: string }>();
  if (existing?.request_digest !== input.requestDigest) throw new Error('operation_conflict');
  return 'existing';
}

export async function completeOperation(db: SqlDatabase, input: Operation, result: string): Promise<void> {
  const update = await db.prepare(`UPDATE operations SET state = 'completed', result = ?, updated_at = ?
    WHERE contributor_id = ? AND kind = ? AND operation_id = ?
    AND request_digest = ? AND state = 'pending'`).bind(
    result, input.now, input.contributorId, input.kind, input.operationId, input.requestDigest,
  ).run();
  if (update.meta.changes !== 1) throw new Error('operation_conflict');
}

export async function reserveNonce(db: SqlDatabase, keyId: string, nonce: string,
  expiresAt: number): Promise<void> {
  await db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at) VALUES (?, ?, ?)')
    .bind(keyId, nonce, expiresAt).run();
}

export async function admitSignedMutation(db: SqlDatabase, input: Operation & {
  keyId: string; nonce: string; expiresAt: number;
}): Promise<'created' | 'existing'> {
  const results = await db.batch([
    db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id)
      VALUES (?, ?, ?, ?)`).bind(input.keyId, input.nonce, input.expiresAt, input.contributorId),
    db.prepare(`INSERT INTO operations
      (contributor_id, kind, operation_id, request_digest, state, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'pending', ?, ?)
      ON CONFLICT(contributor_id, kind, operation_id) DO NOTHING`).bind(
      input.contributorId, input.kind, input.operationId, input.requestDigest,
      input.now, input.now,
    ),
  ]);
  const operation = await db.prepare(`SELECT request_digest, state FROM operations
    WHERE contributor_id = ? AND kind = ? AND operation_id = ?`).bind(
    input.contributorId, input.kind, input.operationId,
  ).first<{ request_digest: string; state: string }>();
  if (operation?.request_digest !== input.requestDigest) throw new Error('operation_conflict');
  return results[1].meta.changes === 1 ? 'created' : 'existing';
}

export async function pruneExpiredAuthenticationState(db: SqlDatabase, now: number,
  limit: number): Promise<void> {
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(now * 1000) ||
      !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) throw new Error('invalid_limit');
  await db.batch([
    transientStatement(db, 'requestNonces', now * 1000, limit, 'prune'),
    transientStatement(db, 'enrollmentStates', now * 1000, limit, 'prune'),
  ]);
}