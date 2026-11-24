import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { privateNote } from '../admin/moderation';
import { RouteError } from '../http/contributor-route';
import { completionStatements, replayOperation } from '../operations/completed';

const token = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
export const reportInput = z.strictObject({ version: z.literal(1),
  target: z.strictObject({ type: z.enum(['filter', 'version']), id: token }),
  reason: z.enum(['spam', 'inaccurate', 'privacy', 'rights', 'other']), note: privateNote.optional() });
export interface ReportResult { version: 1; operationId: string; reportId: string; state: 'open' }

/**
 * §7.6 eligibility: a version report needs a receipt for the version (or a version merged into
 * it); a filter report needs a receipt whose snapshot contained the filter.
 */
async function eligible(db: SqlDatabase, contributorId: string, target: z.infer<typeof reportInput>['target']) {
  const row = target.type === 'version'
    ? await db.prepare(`SELECT 1 AS held FROM download_receipts WHERE contributor_id = ?1 AND (version_id = ?2
        OR version_id IN (SELECT version_id FROM versions WHERE merged_into = ?2))`).bind(contributorId, target.id).first()
    : await db.prepare(`SELECT 1 AS held FROM download_receipts d JOIN master_revision_filters f
        ON f.version_id = d.version_id AND f.revision = d.revision WHERE d.contributor_id = ? AND f.filter_id = ?`)
      .bind(contributorId, target.id).first();
  if (!row) throw new RouteError(403, 'report_ineligible');
}

/** File one private report on a filter or version, with its exact retry result and nonce, atomically. */
export async function fileReport(db: SqlDatabase, identity: AuthenticatedContributor, value: unknown, now: number): Promise<ReportResult> {
  const parsed = reportInput.safeParse(value);
  if (!parsed.success || !identity.operationId) throw new RouteError(400, 'invalid_report');
  const input = parsed.data, digest = identity.contentDigest;
  const replayed = await replayOperation<ReportResult>(db, identity, 'report', digest);
  if (replayed) return replayed;
  await eligible(db, identity.contributorId, input.target);
  const result: ReportResult = { version: 1, operationId: identity.operationId, reportId: crypto.randomUUID(), state: 'open' };
  try {
    await db.batch([...completionStatements(db, identity, 'report', digest, result, now),
      db.prepare(`INSERT INTO reports (report_id, contributor_id, target_type, target_id, key_id, reason, note, state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)`).bind(result.reportId, identity.contributorId, input.target.type, input.target.id,
        identity.keyId, input.reason, input.note ?? '', now)]);
  } catch (error) {
    const raced = await replayOperation<ReportResult>(db, identity, 'report', digest);
    if (raced) return raced;
    if (String(error).includes('UNIQUE constraint failed: reports.contributor_id')) throw new RouteError(409, 'report_already_open');
    throw error;
  }
  return result;
}
