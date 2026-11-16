import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { privateNote } from '../admin/moderation';
import { activePublicationKey } from './publication';

export const reportInput = z.strictObject({ version: z.literal(1),
  reason: z.enum(['spam', 'inaccurate', 'privacy', 'rights', 'other']), note: privateNote.optional() });
export const reportLimits = z.strictObject({ requestBytes: z.number().int().min(1).max(8192),
  maxQueries: z.number().int().min(1).max(64) });
export type ReportLimits = z.infer<typeof reportLimits>;
export interface ReportResult { version: 1; operationId: string; reportId: string; revisionId: string; state: 'open' }

async function eligibleRevision(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string) {
  const row = await db.prepare(`SELECT r.contributor_id AS author, i.counted FROM contribution_revisions r
    JOIN contribution_publications p USING (revision_id) JOIN contributors c ON c.contributor_id = r.contributor_id
    LEFT JOIN import_receipts i ON i.revision_id = r.revision_id AND i.contributor_id = ?
    WHERE r.revision_id = ? AND p.state = 'published' AND c.state = 'active'`)
    .bind(identity.contributorId, revisionId).first<{ author: string; counted: number | null }>();
  if (!row) throw new Error('revision_not_available');
  if (row.author === identity.contributorId || row.counted !== 1) throw new Error('report_ineligible');
}
async function operation(db: SqlDatabase, identity: AuthenticatedContributor) {
  return db.prepare(`SELECT request_digest, result FROM operations WHERE contributor_id = ?
    AND kind = 'report' AND operation_id = ? AND state = 'completed'`).bind(identity.contributorId, identity.operationId!)
    .first<{ request_digest: string; result: string }>();
}

/** Save a private report, exact retry result and nonce atomically. */
export async function submitReport(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string,
  value: unknown | (() => unknown), now: number, limits: ReportLimits): Promise<ReportResult> {
  reportLimits.parse(limits);
  const input = reportInput.parse(typeof value === 'function' ? value() : value);
  if (!identity.operationId) throw new Error('invalid_report');
  await eligibleRevision(db, identity, revisionId);
  if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
  const digest = JSON.stringify([revisionId, identity.contentDigest]);
  const reply = (row: NonNullable<Awaited<ReturnType<typeof operation>>>) => {
    if (row.request_digest !== digest) throw new Error('operation_conflict');
    return JSON.parse(row.result) as ReportResult;
  };
  const old = await operation(db, identity);
  if (old) return reply(old);
  const result: ReportResult = { version: 1, operationId: identity.operationId, reportId: crypto.randomUUID(), revisionId, state: 'open' };
  try {
    await db.batch([
      db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
        .bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
      db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (?, 'report', ?, ?, 'completed', ?, ?, ?)`).bind(identity.contributorId, identity.operationId, digest, JSON.stringify(result), now * 1000, now * 1000),
      db.prepare(`INSERT INTO reports (report_id, contributor_id, revision_id, key_id, reason, note, state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', ?)`).bind(result.reportId, identity.contributorId, revisionId,
        identity.keyId, input.reason, input.note ?? '', now),
    ]);
  } catch (error) {
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    await eligibleRevision(db, identity, revisionId);
    const raced = await operation(db, identity);
    if (raced) return reply(raced);
    if (String(error).includes('UNIQUE constraint failed: reports.contributor_id')) throw new Error('report_already_open');
    throw error;
  }
  return result;
}