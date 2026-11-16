import type { SqlDatabase } from '../runtime/contracts';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { activePublicationKey } from './publication';
import { verifyReceipt, type ReceiptKey } from './receipts';

export interface ImportResult {
  version: 1; operationId: string; revisionId: string; artifactDigest: string;
  receiptId: string; counted: boolean;
}

async function operation(db: SqlDatabase, contributorId: string, operationId: string) {
  return db.prepare(`SELECT request_digest, result FROM operations
    WHERE contributor_id = ? AND kind = 'receipt' AND operation_id = ? AND state = 'completed'`)
    .bind(contributorId, operationId).first<{ request_digest: string; result: string }>();
}

/** Verify the service receipt and commit nonce, retry result and unique import together. */
export async function acknowledgeImport(db: SqlDatabase, identity: AuthenticatedContributor,
  service: string, receiptValue: unknown, keys: ReceiptKey[], now: number): Promise<ImportResult> {
  if (!identity.operationId) throw new Error('invalid_import_receipt');
  const value = typeof receiptValue === 'function' ? receiptValue() : receiptValue;
  const raw = value as { receipt?: { revisionId?: string; artifactDigest?: string } };
  const receipt = await verifyReceipt(value, { service, contributorId: identity.contributorId,
    revisionId: raw?.receipt?.revisionId ?? '', artifactDigest: raw?.receipt?.artifactDigest ?? '' }, keys);
  if (identity.operationId !== `import-${receipt.receipt.receiptId}`) throw new Error('invalid_import_receipt');
  const revision = await db.prepare(`SELECT contributor_id, artifact_digest FROM contribution_revisions WHERE revision_id = ?`)
    .bind(receipt.receipt.revisionId).first<{ contributor_id: string; artifact_digest: string }>();
  if (!revision || revision.artifact_digest !== receipt.receipt.artifactDigest) throw new Error('invalid_import_receipt');
  const result: ImportResult = { version: 1, operationId: identity.operationId,
    revisionId: receipt.receipt.revisionId, artifactDigest: receipt.receipt.artifactDigest,
    receiptId: receipt.receipt.receiptId, counted: revision.contributor_id !== identity.contributorId };
  const existing = await operation(db, identity.contributorId, identity.operationId);
  if (existing && existing.request_digest !== receipt.receipt.receiptId) throw new Error('operation_conflict');
  if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
  if (existing) return JSON.parse(existing.result) as ImportResult;
  try {
    await db.batch([
      db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
        .bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
      db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (?, 'receipt', ?, ?, 'completed', ?, ?, ?)`)
        .bind(identity.contributorId, identity.operationId, receipt.receipt.receiptId, JSON.stringify(result), now * 1000, now * 1000),
      db.prepare(`INSERT INTO import_receipts (contributor_id, revision_id, receipt_id, artifact_digest, counted, acknowledged_at)
        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(contributor_id, revision_id) DO NOTHING`)
        .bind(identity.contributorId, result.revisionId, result.receiptId, result.artifactDigest, Number(result.counted), now),
    ]);
  } catch (error) {
    const raced = await operation(db, identity.contributorId, identity.operationId);
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    if (!raced) throw error;
    if (raced.request_digest !== receipt.receipt.receiptId) throw new Error('operation_conflict');
    return JSON.parse(raced.result) as ImportResult;
  }
  return result;
}

/** Rebuild only import counters for a bounded, explicitly supplied revision batch. */
export async function reconcileImportCounts(db: SqlDatabase, revisionIds: string[]) {
  if (revisionIds.length > 100 || revisionIds.some(id => !/^[A-Za-z0-9_.-]{1,128}$/.test(id)))
    throw new Error('invalid_reconciliation');
  if (!revisionIds.length) return [];
  return db.batch([...new Set(revisionIds)].map(id => db.prepare(`UPDATE contribution_statistics SET imports =
    (SELECT count(*) FROM import_receipts WHERE revision_id = ? AND counted = 1) WHERE revision_id = ?`).bind(id, id)));
}