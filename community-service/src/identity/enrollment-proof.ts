import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { readBoundedBytes } from '../http/bounded-body';
import { verifyRequestSignature, RequestSignatureTimeError } from '../security/request-signatures';
import { enroll } from './repository';
import { confirmationHash } from './confirmation';

const payloadSchema = z.object({
  version: z.literal(1),
  stateId: z.string().regex(/^[0-9a-f]{64}$/),
  installationId: z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/),
  confirmationSecret: z.string().regex(/^[0-9a-f]{64}$/).optional(),
}).strict();

export class EnrollmentProofError extends Error {
  constructor(public code: 'invalid_enrollment_proof' | 'enrollment_conflict' | 'display_name_taken' |
    'recovery_required' | 'key_inactive') {
    super(code);
  }
}

type ProofPolicy = {
  authority: string;
  now: number;
  maxLifetimeSeconds: number;
  maxClockSkewSeconds: number;
  displayName: string;
  requireConfirmation?: boolean;
  beforeRegister?: (subject: string) => Promise<void>;
};

/** Verify a grant-bound key proof before atomically registering its installation. */
export async function completeEnrollmentProof(request: Request, db: SqlDatabase,
  policy: ProofPolicy): Promise<{ contributorId: string; installationId: string; keyId: string }> {
  let payload: z.infer<typeof payloadSchema>;
  let body: Uint8Array;
  try {
    if (!Number.isSafeInteger(policy.now) || policy.now < 0 || request.method !== 'POST' ||
        request.url !== `https://${policy.authority}/v1/enrollments/complete` ||
        request.headers.get('content-type') !== 'application/json' ||
        request.headers.has('content-encoding')) throw new Error('invalid');
    body = await readBoundedBytes(request, 1024);
    payload = payloadSchema.parse(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)));
    if (request.headers.get('idempotency-key') !== payload.stateId) throw new Error('invalid');
  } catch {
    throw new EnrollmentProofError('invalid_enrollment_proof');
  }
  if (await db.prepare("SELECT 1 FROM operations WHERE kind = 'key_recovery_authorization' AND operation_id = ?")
    .bind(payload.stateId).first()) throw new EnrollmentProofError('recovery_required');
  const grant = await db.prepare(`SELECT *
    FROM enrollment_states WHERE state_id = ? AND expires_at > ?
      AND provider_subject IS NOT NULL`).bind(payload.stateId, policy.now).first<{
        installation_id: string; key_id: string; public_key: ArrayBuffer; provider_subject: string;
        confirmation_hash?: string | null; consumed_at: number | null;
      }>();
  if (!grant || grant.installation_id !== payload.installationId) {
    throw new EnrollmentProofError('invalid_enrollment_proof');
  }
  if (policy.requireConfirmation && (!payload.confirmationSecret || !grant.confirmation_hash ||
      await confirmationHash(payload.confirmationSecret) !== grant.confirmation_hash)) {
    throw new EnrollmentProofError('invalid_enrollment_proof');
  }
  if (grant.consumed_at !== null && !policy.requireConfirmation) {
    throw new EnrollmentProofError('invalid_enrollment_proof');
  }
  const publicKey = new Uint8Array(grant.public_key);
  let verified: Awaited<ReturnType<typeof verifyRequestSignature>>;
  try {
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey));
    const keyId = `sha256:${Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')}`;
    if (keyId !== grant.key_id) throw new Error('invalid');
    verified = await verifyRequestSignature({ method: request.method, url: request.url,
      headers: request.headers, body }, publicKey, { now: Math.floor(policy.now / 1000),
      expectedAuthority: policy.authority, expectedKeyId: grant.key_id,
      maxLifetimeSeconds: policy.maxLifetimeSeconds, maxClockSkewSeconds: policy.maxClockSkewSeconds });
  } catch (error) {
    if (error instanceof RequestSignatureTimeError) throw error;
    throw new EnrollmentProofError('invalid_enrollment_proof');
  }
  if (policy.beforeRegister) await policy.beforeRegister(grant.provider_subject);
  const reconciled = async () => {
    const registered = await db.prepare(`SELECT contributor.contributor_id FROM installation_keys AS key
      JOIN installations AS installation ON installation.installation_id = key.installation_id
      JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
      JOIN enrollment_states AS grant ON grant.state_id = ?
      WHERE grant.consumed_at IS NOT NULL AND grant.expires_at > ?
        AND grant.installation_id = ? AND grant.key_id = ? AND grant.public_key = ?
        AND grant.provider_subject = contributor.discord_user_id
        AND key.installation_id = grant.installation_id AND key.key_id = grant.key_id
        AND key.public_key = grant.public_key AND key.state = 'active' AND contributor.state = 'active'
        AND grant.confirmation_hash = ?`).bind(payload.stateId, policy.now, grant.installation_id,
      grant.key_id, grant.public_key, grant.confirmation_hash ?? null)
      .first<{ contributor_id: string }>();
    if (!registered) throw new EnrollmentProofError('enrollment_conflict');
    return { contributorId: registered.contributor_id,
      installationId: grant.installation_id, keyId: grant.key_id };
  };
  if (grant.consumed_at !== null) return reconciled();
  if (policy.requireConfirmation) {
    const registeredKey = await db.prepare('SELECT state FROM installation_keys WHERE key_id = ?')
      .bind(grant.key_id).first<{ state: string }>();
    if (registeredKey && registeredKey.state !== 'active') throw new EnrollmentProofError('key_inactive');
    const existing = await db.prepare(`SELECT contributor.contributor_id FROM installation_keys AS key
      JOIN installations AS installation ON installation.installation_id = key.installation_id
      JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
      WHERE key.key_id = ? AND key.installation_id = ? AND key.public_key = ?
        AND key.state = 'active' AND contributor.state = 'active' AND contributor.discord_user_id = ?`)
      .bind(grant.key_id, grant.installation_id, grant.public_key, grant.provider_subject)
      .first<{ contributor_id: string }>();
    if (existing) {
      try {
        await db.batch([db.prepare(`UPDATE enrollment_states SET consumed_at = ?
        WHERE state_id = ? AND consumed_at IS NULL AND expires_at > ? AND confirmation_hash = ?
          AND installation_id = ? AND key_id = ? AND public_key = ? AND provider_subject = ?
          AND EXISTS (SELECT 1 FROM installation_keys AS key
            JOIN installations AS installation ON installation.installation_id = key.installation_id
            JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
            WHERE key.key_id = enrollment_states.key_id AND key.public_key = enrollment_states.public_key
              AND key.installation_id = enrollment_states.installation_id AND key.state = 'active'
              AND contributor.state = 'active' AND contributor.discord_user_id = enrollment_states.provider_subject)`)
        .bind(policy.now, payload.stateId, policy.now, grant.confirmation_hash, grant.installation_id,
          grant.key_id, grant.public_key, grant.provider_subject),
        db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id)
          VALUES (CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?, ?, ?)`)
          .bind(grant.key_id, verified.nonce, verified.expires + policy.maxClockSkewSeconds,
            existing.contributor_id),
        ]);
      } catch { return reconciled(); }
      return reconciled();
    }
    const installation = await db.prepare(`SELECT contributor.discord_user_id FROM installations AS installation
      JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
      WHERE installation.installation_id = ?`).bind(grant.installation_id)
      .first<{ discord_user_id: string }>();
    if (installation?.discord_user_id === grant.provider_subject) {
      throw new EnrollmentProofError('recovery_required');
    }
  }
  try {
    const contributor = await enroll(db, { stateId: payload.stateId,
      discordUserId: grant.provider_subject, contributorId: crypto.randomUUID(),
      installationId: grant.installation_id, keyId: grant.key_id, publicKey,
      displayName: policy.displayName, now: policy.now,
      confirmationHash: policy.requireConfirmation ? grant.confirmation_hash! : undefined,
      proof: { nonce: verified.nonce,
        expiresAt: verified.expires + policy.maxClockSkewSeconds } });
    return { contributorId: contributor.contributor_id,
      installationId: grant.installation_id, keyId: grant.key_id };
  } catch (error) {
    if (policy.requireConfirmation) {
      try { return await reconciled(); }
      catch {
        if (String(error).includes('contributors.display_name_key')) throw new EnrollmentProofError('display_name_taken');
      }
    }
    throw new EnrollmentProofError('enrollment_conflict');
  }
}