import type { SqlDatabase } from '../runtime/contracts';
import { verifyArtifactSignature } from '../artifact/signatures';
import { recordUsage, type UsageResource } from '../budgets/usage';
import { sha256, timestamp, token } from '../catalog/records';
import { observeOAuthDatabase } from '../integrations/discord/usage';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { publicationLimits, type PublicationInput, type PublicationLimits } from './publication-input';
import { publicationStatement, readReservation, revisionStatements } from './repository';
import { publicationStorageStatement } from './storage';
import { requireSharingPermission } from './permission';

const kind = 'publication';
export interface PublicationResult {
  version: 1; operationId: string; contributionId: string; revisionId: string;
  artifactDigest: string; committedAt: number;
}
type OperationRow = { request_digest: string; state: string; result: string | null };

export async function publicationOperation(db: SqlDatabase, contributorId: string, operationId: string) {
  return db.prepare(`SELECT request_digest, state, result FROM operations
    WHERE contributor_id = ? AND kind = 'publication' AND operation_id = ?`)
    .bind(contributorId, token(operationId)).first<OperationRow>();
}

export async function activePublicationKey(db: SqlDatabase, identity: AuthenticatedContributor) {
  return db.prepare(`SELECT key.public_key FROM installation_keys AS key
    JOIN installations AS installation USING (installation_id)
    JOIN contributors AS contributor USING (contributor_id)
    WHERE key.key_id = ? AND key.state = 'active' AND contributor.state = 'active'
      AND contributor.contributor_id = ?`).bind(identity.keyId, identity.contributorId)
    .first<{ public_key: ArrayBuffer }>();
}

export async function publishArtifact(db: SqlDatabase,
  identity: AuthenticatedContributor, inputValue: PublicationInput | (() => PublicationInput), limits: PublicationLimits, now: number) {
  publicationLimits(limits); timestamp(now);
  if (!identity.operationId) throw new Error('invalid_publication');
  const operation = { kind, id: token(identity.operationId) };
  const observer = observeOAuthDatabase(db);
  db = observer.db;
  const observed: Partial<Record<UsageResource, number>> = { worker_requests: 1 };
  let outcome: 'allowed' | 'denied' | 'retried' = 'denied';
  let usageAvailable = true;
  let successful = false;
  let response: { result: PublicationResult; usageAvailable: boolean } | undefined;
  try {
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    const input = typeof inputValue === 'function' ? inputValue() : inputValue;
    const started = Date.now();
    if (input.artifactBytes.length > limits.artifact.compressedBytes || input.manifestBytes.length > limits.manifestBytes ||
        input.envelopeBytes.length > 256) throw new Error('publication_size_limit');
    let old = await publicationOperation(db, identity.contributorId, operation.id);
    if (old && old.request_digest !== identity.contentDigest) throw new Error('operation_conflict');
    const reservation = old ? await readReservation(db, identity.contributorId, operation) : null;
    if (old?.state === 'pending' && reservation?.state === 'published')
      old = await publicationOperation(db, identity.contributorId, operation.id);
    if (old && !reservation) throw new Error('publication_unavailable');
    const signingKeyId = reservation?.key_id ?? identity.keyId;
    const key = await db.prepare(`SELECT key.public_key, key.state FROM installation_keys AS key
      JOIN installations AS installation USING (installation_id)
      WHERE key.key_id = ? AND installation.contributor_id = ?`)
      .bind(signingKeyId, identity.contributorId).first<{ public_key: ArrayBuffer; state: string }>();
    if (!key || (key.state !== 'active' && !(reservation && key.state === 'retired'))) throw new Error('key_inactive');
    const digests = await Promise.all([input.artifactBytes, input.manifestBytes, input.envelopeBytes].map(sha256));
    const checkRevision = (row: typeof reservation) => {
      if (row && (row.revision_id !== input.submissionId || row.contribution_id !== input.contributionId ||
          row.artifact_digest !== digests[0] || row.manifest_digest !== digests[1] || row.envelope_digest !== digests[2]))
        throw new Error('operation_conflict');
    };
    checkRevision(reservation);
    // A reservation holds bytes the service already accepted, possibly before migration 0017.
    const verified = await verifyArtifactSignature(input.artifactBytes, input.manifestBytes, input.envelopeBytes,
      { contributorId: identity.contributorId, keyId: signingKeyId, publicKey: new Uint8Array(key.public_key),
        status: key.state as 'active' | 'retired' }, input.submissionId, limits.artifact, Boolean(reservation));
    if (Date.now() - started > limits.validationWallMs) throw new Error('validation_budget_exceeded');
    if (old?.state === 'completed') {
      if (reservation?.state !== 'published' || !old.result) throw new Error('publication_not_available');
      outcome = 'retried';
      successful = true;
      return response = { result: JSON.parse(old.result) as PublicationResult, usageAvailable };
    }
    if (old && old.state !== 'pending') throw new Error('publication_not_available');
    if (reservation && !['reserved', 'object_stored'].includes(reservation.state)) throw new Error('publication_not_available');
    requireSharingPermission(verified.artifact.artifact);
    const validatedPlan = await revisionStatements(db, { verified, contributionId: input.contributionId,
      releaseId: crypto.randomUUID(), now, limits: limits.catalog, operation, permissionBounds: limits.artifact });
    if (Date.now() - started > limits.validationWallMs) throw new Error('validation_budget_exceeded');
    const plan = reservation ? null : validatedPlan;
    const revisionId = reservation?.revision_id ?? plan!.revisionId;
    const objectKey = reservation?.object_key ?? plan!.objectKey;
    const storage = await publicationStorageStatement(db, objectKey, {
      artifact: { bytes: input.artifactBytes, digest: digests[0] },
      manifest: { bytes: input.manifestBytes, digest: digests[1] },
      envelope: { bytes: input.envelopeBytes, digest: digests[2] },
    });
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    const result: PublicationResult = { version: 1, operationId: operation.id, contributionId: input.contributionId,
      revisionId, artifactDigest: digests[0], committedAt: now };
    // New publication state survives only if this entire batch commits. Legacy pending
    // rows can still be completed by an explicit retry without a recovery service.
    try { await db.batch([
      db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)`)
        .bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
      ...(plan ? [db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, created_at, updated_at)
        VALUES (?, 'publication', ?, ?, 'pending', ?, ?)`).bind(identity.contributorId, operation.id, identity.contentDigest, now * 1000, now * 1000),
        ...plan.statements] : []),
      storage,
      publicationStatement(db, { revisionId, from: 'reserved', to: 'object_stored',
        objectEtag: reservation?.object_etag ?? digests[0], now }),
      db.prepare(`UPDATE operations SET state = CASE WHEN EXISTS (
        SELECT 1 FROM installation_keys AS key JOIN installations AS installation USING (installation_id)
        JOIN contributors AS contributor USING (contributor_id)
        WHERE key.key_id = ? AND key.state = 'active' AND contributor.state = 'active' AND contributor.contributor_id = ?
        ) THEN state ELSE 'invalid' END WHERE contributor_id = ? AND kind = 'publication' AND operation_id = ?`)
        .bind(identity.keyId, identity.contributorId, identity.contributorId, operation.id),
      publicationStatement(db, { revisionId, from: 'object_stored', to: 'published', now }),
      db.prepare(`UPDATE operations SET state = 'completed', result = ?, updated_at = ?
        WHERE contributor_id = ? AND kind = 'publication' AND operation_id = ? AND state = 'pending'
        AND EXISTS (SELECT 1 FROM contribution_publications WHERE revision_id = ? AND state = 'published')`)
        .bind(JSON.stringify(result), now * 1000, identity.contributorId, operation.id, revisionId),
    ]); }
    catch (error) {
      // A concurrent identical request or a lost commit response may already have
      // succeeded. Read its result; never start another publication to recover it.
      const raced = await publicationOperation(db, identity.contributorId, operation.id);
      if (!raced) throw error;
      if (raced.request_digest !== identity.contentDigest) throw new Error('operation_conflict');
      const revision = await readReservation(db, identity.contributorId, operation);
      checkRevision(revision);
      if (raced.state !== 'completed' || revision?.state !== 'published' || !raced.result) throw error;
      outcome = 'retried';
      successful = true;
      return response = { result: JSON.parse(raced.result) as PublicationResult, usageAvailable };
    }
    outcome = reservation ? 'retried' : 'allowed';
    const completed = await publicationOperation(db, identity.contributorId, operation.id);
    if (completed?.state !== 'completed' || !completed.result) throw new Error('publication_unavailable');
    successful = true;
    return response = { result: JSON.parse(completed.result) as PublicationResult, usageAvailable };
  } finally {
    if (!successful) outcome = 'denied';
    for (const [resource, units] of Object.entries(observed)) {
      try { await recordUsage(db, { contributorId: identity.contributorId, resource: resource as UsageResource,
        outcome, units, now: now * 1000 }); }
      catch { usageAvailable = false; }
    }
    const written = observer.rowsWritten();
    if (written === null) usageAvailable = false;
    else {
      try { await recordUsage(db, { contributorId: identity.contributorId, resource: 'd1_writes', outcome,
        units: written + 1, now: now * 1000 }); }
      catch { usageAvailable = false; }
    }
    if (response) response.usageAvailable = usageAvailable;
  }
}