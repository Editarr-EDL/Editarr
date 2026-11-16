import type { SqlDatabase } from '../runtime/contracts';
import { transientStatement } from '../retention/transient';
import { z } from 'zod';
import { readBoundedBytes } from '../http/bounded-body';
import { verifyRequestSignature } from '../security/request-signatures';
import { confirmationHash } from './confirmation';

const uuid = z.string().uuid();
const fingerprint = z.string().regex(/^sha256:[0-9a-f]{64}$/);
const payloadSchema = z.object({ version: z.literal(1), stateId: z.string().regex(/^[0-9a-f]{64}$/),
  operationId: uuid, installationId: uuid, oldKeyId: fingerprint,
  confirmationSecret: z.string().regex(/^[0-9a-f]{64}$/) }).strict();

export type RecoveryAuthorization = { version: 1; operationId: string; installationId: string;
  oldKeyId: string; newKeyId: string; publicKey: string; confirmationHash: string; expiresAt: number };

export async function recoveryAuthorization(db: SqlDatabase, stateId: string) {
  const row = await db.prepare(`SELECT contributor_id, result FROM operations
    WHERE kind = 'key_recovery_authorization' AND operation_id = ? AND state = 'completed'`)
    .bind(stateId).first<{ contributor_id: string; result: string }>();
  return row ? { contributorId: row.contributor_id,
    intent: JSON.parse(row.result) as RecoveryAuthorization } : null;
}

/** Verify fresh Discord ownership and candidate possession, never an old private key. */
export async function recoverInstallation(request: Request, db: SqlDatabase, authority: string,
  now: number, membership: (subject: string, contributorId: string) => Promise<void>) {
  if (!Number.isSafeInteger(now) || now < 0) throw new Error('invalid_recovery');
  const status = request.url === `https://${authority}/v1/keys/recovery-status`;
  if (request.method !== 'POST' || (!status && request.url !== `https://${authority}/v1/keys/recover`) ||
      request.headers.get('content-type') !== 'application/json' || request.headers.has('content-encoding')) {
    throw new Error('invalid_recovery');
  }
  const body = await readBoundedBytes(request, 2048);
  const payload = payloadSchema.parse(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)));
  if (request.headers.get('idempotency-key') !== payload.operationId) throw new Error('invalid_recovery');
  const authorization = await recoveryAuthorization(db, payload.stateId);
  if (!authorization) throw new Error('invalid_recovery');
  const intent = authorization.intent;
  if (intent.operationId !== payload.operationId || intent.installationId !== payload.installationId ||
      intent.oldKeyId !== payload.oldKeyId || await confirmationHash(payload.confirmationSecret) !== intent.confirmationHash) {
    throw new Error('invalid_recovery');
  }
  const grant = await db.prepare(`SELECT grant.*, contributor.discord_user_id FROM enrollment_states AS grant
    JOIN contributors AS contributor ON contributor.contributor_id = ? AND contributor.state = 'active'
    WHERE grant.state_id = ? AND grant.provider_subject = contributor.discord_user_id
      AND grant.installation_id = ? AND grant.key_id = ? AND grant.confirmation_hash = ?`)
    .bind(authorization.contributorId, payload.stateId, intent.installationId, intent.newKeyId,
      intent.confirmationHash).first<{ public_key: ArrayBuffer; expires_at: number;
        consumed_at: number | null; discord_user_id: string }>();
  const contributor = await db.prepare("SELECT discord_user_id FROM contributors WHERE contributor_id = ? AND state = 'active'")
    .bind(authorization.contributorId).first<{ discord_user_id: string }>();
  if (!contributor) throw new Error('invalid_recovery');
  const publicKey = Uint8Array.from(atob(intent.publicKey), (character) => character.charCodeAt(0));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey)),
    (byte) => byte.toString(16).padStart(2, '0')).join('');
  if (`sha256:${digest}` !== intent.newKeyId) throw new Error('invalid_recovery');
  const proof = await verifyRequestSignature({ method: request.method, url: request.url,
    headers: request.headers, body }, publicKey, { expectedAuthority: authority,
    expectedKeyId: intent.newKeyId, now: Math.floor(now / 1000), maxLifetimeSeconds: 120, maxClockSkewSeconds: 5 });
  await membership(contributor.discord_user_id, authorization.contributorId);
  const previous = async () => {
    const row = await db.prepare(`SELECT request_digest, result FROM operations
      WHERE contributor_id = ? AND kind = 'key_recovery' AND operation_id = ? AND state = 'completed'`)
      .bind(authorization.contributorId, payload.operationId).first<{ request_digest: string; result: string }>();
    if (!row) return null;
    if (row.request_digest !== request.headers.get('content-digest')) throw new Error('operation_conflict');
    const active = await db.prepare(`SELECT 1 FROM installation_keys AS key JOIN installations AS installation
      ON installation.installation_id = key.installation_id JOIN contributors AS contributor
      ON contributor.contributor_id = installation.contributor_id WHERE key.key_id = ? AND key.state = 'active'
      AND key.installation_id = ? AND contributor.contributor_id = ? AND contributor.state = 'active'`)
      .bind(intent.newKeyId, intent.installationId, authorization.contributorId).first();
    if (!active) throw new Error('key_inactive');
    return JSON.parse(row.result);
  };
  const completed = await previous();
  if (completed) {
    return { version: 1, state: 'completed', result: completed };
  }
  if (!grant || grant.expires_at <= now || intent.expiresAt <= now || grant.consumed_at !== null) {
    throw new Error('recovery_expired');
  }
  const result = { version: 1, kind: 'key_recovery', operationId: payload.operationId,
    stateId: payload.stateId,
    contributorId: authorization.contributorId, installationId: intent.installationId,
    oldKeyId: intent.oldKeyId, newKeyId: intent.newKeyId, committedAt: now };
  if (status) {
    const target = await db.prepare(`SELECT 1 FROM installation_keys AS old JOIN installations AS installation
      ON installation.installation_id = old.installation_id JOIN contributors AS contributor
      ON contributor.contributor_id = installation.contributor_id WHERE old.installation_id = ? AND old.key_id = ?
      AND contributor.contributor_id = ? AND contributor.state = 'active'
      AND NOT EXISTS (SELECT 1 FROM installation_keys WHERE installation_id = ? AND state = 'active' AND key_id != ?)`)
      .bind(intent.installationId, intent.oldKeyId, authorization.contributorId, intent.installationId, intent.oldKeyId).first();
    if (!target) throw new Error('recovery_conflict');
    return { version: 1, state: 'not_committed', result: null };
  }
  try {
    await db.batch([
      db.prepare(`UPDATE enrollment_states SET consumed_at = ? WHERE state_id = ? AND consumed_at IS NULL
        AND expires_at > ? AND provider_subject = ? AND installation_id = ? AND key_id = ?
        AND public_key = ? AND confirmation_hash = ?
        AND EXISTS (SELECT 1 FROM installations AS installation JOIN contributors AS contributor
          ON contributor.contributor_id = installation.contributor_id JOIN installation_keys AS old
          ON old.installation_id = installation.installation_id WHERE installation.installation_id = ?
          AND contributor.contributor_id = ? AND contributor.discord_user_id = ? AND contributor.state = 'active'
          AND old.key_id = ? AND NOT EXISTS (SELECT 1 FROM installation_keys WHERE installation_id = ?
            AND state = 'active' AND key_id != ?))`)
        .bind(now, payload.stateId, now, grant.discord_user_id, intent.installationId, intent.newKeyId,
          publicKey, intent.confirmationHash, intent.installationId, authorization.contributorId,
          grant.discord_user_id, intent.oldKeyId, intent.installationId, intent.oldKeyId),
      db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (CASE WHEN changes() = 1 THEN ? ELSE NULL END, 'key_recovery', ?, ?, 'completed', ?, ?, ?)`)
        .bind(authorization.contributorId, payload.operationId, request.headers.get('content-digest'), JSON.stringify(result), now, now),
      db.prepare(`UPDATE installation_keys SET state = 'retired', retired_at = ?
        WHERE key_id = ? AND installation_id = ? AND state = 'active'`).bind(now, intent.oldKeyId, intent.installationId),
      db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
        VALUES (?, ?, 'Ed25519', ?, 'active', ?)`).bind(intent.newKeyId, intent.installationId, publicKey, now),
      db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)`)
        .bind(intent.newKeyId, proof.nonce, proof.expires + 5, authorization.contributorId),
    ]);
  } catch (error) {
    const raced = await previous();
    if (raced) return { version: 1, state: 'completed', result: raced };
    throw new Error('recovery_conflict');
  }
  return { version: 1, state: 'completed', result };
}

/** Expire unused browser authority, retaining evidence needed by committed recoveries. */
export async function pruneRecoveryAuthorizations(db: SqlDatabase, now: number, limit: number) {
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) {
    throw new Error('invalid_limit');
  }
  const result = await transientStatement(db, 'recoveryAuthorizations', now, limit, 'prune').run();
  return result.meta.changes;
}