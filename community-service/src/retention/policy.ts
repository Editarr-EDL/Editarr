import type { SqlDatabase } from '../runtime/contracts';
import { RETENTION_BATCH_SIZE, TRANSIENT_CATEGORIES, transientStatement } from './transient';

export const RETENTION_POLICY = Object.freeze({
  version: 'community-retain-first-1',
  durableRecords: 'indefinite',
  payloadDeletion: false,
  manualServiceBackupPruning: false,
  transientExpiry: 'existing_rules',
  batchSizePerCategory: RETENTION_BATCH_SIZE,
});

export async function previewRetention(db: SqlDatabase, now: number) {
  if (!Number.isSafeInteger(now) || now < 0 || now > 8_640_000_000_000_000) throw new Error('invalid_time');
  const categories = [];
  for (const category of TRANSIENT_CATEGORIES) {
    const count = await transientStatement(db, category, now, RETENTION_BATCH_SIZE, 'preview')
      .first<number>('eligible');
    if (count === null || !Number.isSafeInteger(count) || count < 0 || count > RETENTION_BATCH_SIZE + 1) {
      throw new Error('retention_preview_unavailable');
    }
    categories.push({ category, eligible: Math.min(count, RETENTION_BATCH_SIZE),
      moreEligible: count > RETENTION_BATCH_SIZE });
  }
  return { policy: RETENTION_POLICY, observedAt: now, mode: 'read_only',
    scope: 'currently_eligible_not_reserved', payloadBytesReclaimed: 0, categories };
}