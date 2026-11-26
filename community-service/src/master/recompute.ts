import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { mergeVersion, snapshotBytes, type MergePolicy, type MergeResult } from '../filters';
import { isCatalogWriteConflict, workWriteGuard } from '../versions/works';
import { mergeInput, loadVersionState, type VersionState } from './state';

export interface RecomputeOptions {
  /** Snapshot `service`: the public HTTPS origin. */
  service: string;
  policy: MergePolicy;
  /** Unix seconds; the `generatedAt` of a new revision. */
  now: number;
}
export interface RecomputePlan {
  versionId: string;
  result: MergeResult;
  /** The revision the version has after the batch commits. */
  revision: number;
  changed: boolean;
  statements: SqlStatement[];
}

async function sha256Hex(bytes: Uint8Array) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
    byte => byte.toString(16).padStart(2, '0')).join('');
}

const json = (value: unknown) => JSON.stringify(value);

/**
 * Bound retained snapshot bytes (§8): besides the latest revision, a version keeps the bytes of
 * at most `olderRevisions` earlier revisions, and only while a receipt for that revision was
 * issued within `receiptSeconds`. Digests, receipts and `master_revision_filters` are kept.
 */
export const SNAPSHOT_RETENTION = Object.freeze({ olderRevisions: 8, receiptSeconds: 7 * 86_400 });

/**
 * Bound `supersededSnapshots`: the update reads `idx_master_revisions_retained_bytes` (migration 0004),
 * so it visits only the version's revisions that still hold bytes, not its whole history.
 */
export const SUPERSEDED_SNAPSHOTS_SQL = `UPDATE master_revisions SET snapshot_bytes = NULL WHERE version_id = ?1 AND revision < ?2
    AND snapshot_bytes IS NOT NULL AND (NOT EXISTS (SELECT 1 FROM download_receipts r WHERE r.version_id = ?1
      AND r.revision = master_revisions.revision AND r.issued_at > ?3)
    OR revision NOT IN (SELECT revision FROM master_revisions WHERE version_id = ?1 AND revision < ?2
      ORDER BY revision DESC LIMIT ?4))`;

/** Drop superseded snapshot bytes outside `SNAPSHOT_RETENTION`; `latest` is the newest stored revision. */
export function supersededSnapshots(db: SqlDatabase, versionId: string, latest: number, now: number) {
  return db.prepare(SUPERSEDED_SNAPSHOTS_SQL)
    .bind(versionId, latest, now - SNAPSHOT_RETENTION.receiptSeconds, SNAPSHOT_RETENTION.olderRevisions);
}

/**
 * Recompute a version (§6) from `state` and return the statements that persist the master set:
 * registry, filters, attestation assignments, derived entry columns, submission states and fits,
 * runtime, trust and, when served content changed, a new immutable snapshot revision (§6.9).
 * The caller supplies the work write guard; these statements never add one.
 */
export async function recomputeStatements(db: SqlDatabase, state: VersionState, options: RecomputeOptions): Promise<RecomputePlan> {
  const result = mergeVersion(mergeInput(state, options.service), options.policy);
  const versionId = state.versionId, sources = json(state.sourceVersionIds);
  const submissions = json(state.submissions.map(submission => submission.submissionId));
  const statements: SqlStatement[] = [
    db.prepare('DELETE FROM master_filters WHERE version_id IN (SELECT value FROM json_each(?))').bind(sources),
    db.prepare(`INSERT INTO filter_registry (filter_id, version_id, created_seq, incumbent_subcategory, incumbent_action,
        incumbent_expression, state, merged_into, quarantined)
      SELECT value ->> '$.filterId', ?, value ->> '$.createdSeq', value ->> '$.incumbentSubcategory', value ->> '$.incumbentAction',
        value ->> '$.incumbentExpression', value ->> '$.state', value ->> '$.mergedInto', value ->> '$.quarantined' FROM json_each(?) WHERE true
      ON CONFLICT(filter_id) DO UPDATE SET version_id = excluded.version_id, created_seq = excluded.created_seq,
        incumbent_subcategory = excluded.incumbent_subcategory, incumbent_action = excluded.incumbent_action,
        incumbent_expression = excluded.incumbent_expression, state = excluded.state, merged_into = excluded.merged_into, quarantined = excluded.quarantined`)
      .bind(versionId, json(result.registry.map(row => ({ ...row, quarantined: Number(row.quarantined) })))),
    db.prepare(`INSERT INTO master_filters (filter_id, version_id, category, subcategory, severity, action, expression, start_ms, end_ms,
        spread_start_ms, spread_end_ms, confidence, contributor_count, attestations, upvotes, downvotes, disputes,
        coverage_negatives, low_confidence, served, subcategory_tally_json, severity_tally_json, action_tally_json)
      SELECT value ->> '$.filterId', ?, value ->> '$.category', value ->> '$.subcategory', value ->> '$.severity',
        value ->> '$.action', value ->> '$.expression', value ->> '$.startMs', value ->> '$.endMs', value ->> '$.spread.startMs', value ->> '$.spread.endMs',
        value ->> '$.confidence', value ->> '$.contributorCount', value ->> '$.support.attestations', value ->> '$.support.upvotes',
        value ->> '$.negative.downvotes', value ->> '$.negative.disputes', value ->> '$.negative.coverageNegatives',
        value ->> '$.lowConfidence', value ->> '$.served', value -> '$.tallies.subcategory', value -> '$.tallies.severity',
        value -> '$.tallies.action' FROM json_each(?)`)
      .bind(versionId, json(result.filters.map(filter => ({ ...filter, lowConfidence: Number(filter.lowConfidence),
        served: Number(filter.served) })))),
    db.prepare(`UPDATE filter_attestations SET filter_id = NULL, counted = 0
      WHERE submission_id IN (SELECT value FROM json_each(?))`).bind(submissions),
    db.prepare(`DELETE FROM filter_attestations WHERE implicit = 1 AND submission_id IN (SELECT value FROM json_each(?))`).bind(submissions),
    db.prepare(`INSERT INTO filter_attestations (submission_id, entry_id, filter_id, contributor_id, kind, implicit, counted, prior_filter_id)
      SELECT value ->> '$.submissionId', value ->> '$.entryId', value ->> '$.filterId', value ->> '$.contributorId',
        value ->> '$.kind', value ->> '$.implicit', value ->> '$.counted', value ->> '$.filterId' FROM json_each(?) WHERE true
      ON CONFLICT(submission_id, entry_id) DO UPDATE SET filter_id = excluded.filter_id, kind = excluded.kind,
        implicit = excluded.implicit, counted = excluded.counted,
        prior_filter_id = coalesce(excluded.filter_id, filter_attestations.prior_filter_id)`)
      .bind(json(result.attestations.map(row => ({ ...row, implicit: Number(row.implicit), counted: Number(row.counted) })))),
    db.prepare(`UPDATE submission_entries SET aligned_start_ms = NULL, aligned_end_ms = NULL, weight = 0, ignored_reason = NULL
      WHERE submission_id IN (SELECT value FROM json_each(?))`).bind(submissions),
    db.prepare(`UPDATE submission_entries SET aligned_start_ms = j.value ->> '$.alignedStartMs', aligned_end_ms = j.value ->> '$.alignedEndMs',
        weight = j.value ->> '$.weight', ignored_reason = j.value ->> '$.ignoredReason'
      FROM json_each(?) AS j WHERE submission_entries.submission_id = j.value ->> '$.submissionId'
        AND submission_entries.entry_id = j.value ->> '$.entryId'`)
      .bind(json(result.attestations.filter(row => !row.implicit))),
    db.prepare(`UPDATE submissions SET state = j.value ->> '$.state', fit_json = j.value -> '$.fit'
      FROM json_each(?) AS j WHERE submissions.submission_id = j.value ->> '$.submissionId'`).bind(json(result.submissions)),
    db.prepare(`UPDATE submissions SET fit_json = NULL WHERE fit_json = 'null' AND submission_id IN (SELECT value FROM json_each(?))`)
      .bind(submissions),
    db.prepare('UPDATE versions SET runtime_ms = ?, trust = ?, revision = max(revision, ?) WHERE version_id = ?')
      .bind(result.runtimeMs, result.trust, result.revision.revision, versionId),
  ];
  if (result.revision.changed) {
    const bytes = snapshotBytes(result.servedContent, result.revision.revision, options.now);
    statements.push(
      db.prepare(`INSERT INTO master_revisions (version_id, revision, snapshot_digest, generated_at, snapshot_bytes)
        VALUES (?, ?, ?, ?, ?)`).bind(versionId, result.revision.revision, await sha256Hex(bytes), options.now, bytes),
      supersededSnapshots(db, versionId, result.revision.revision, options.now));
  }
  return { versionId, result, revision: result.revision.revision, changed: result.revision.changed, statements };
}

/** A change committed with a recompute: `change` applies it to the loaded state, `extra` writes it. */
export interface PendingChange {
  change?: (state: VersionState) => void;
  extra?: (plan: RecomputePlan) => SqlStatement[];
}

/**
 * Recompute one stored version in its own guarded batch, planning again after a concurrent
 * catalog write. A pending change (for example a vote) is applied to the loaded inputs and its
 * statements join the same batch. `target` may resolve the version on each attempt.
 */
export async function recomputeVersion(db: SqlDatabase, target: string | (() => Promise<string>), options: RecomputeOptions,
  pending: PendingChange = {}, attempts = 5) {
  for (let attempt = 1; ; attempt++) {
    const versionId = typeof target === 'string' ? target : await target();
    const row = await db.prepare('SELECT work_id FROM versions WHERE version_id = ?').bind(versionId).first<{ work_id: string }>();
    if (!row) throw new Error('version_not_found');
    const guard = await workWriteGuard(db, row.work_id);
    const state = await loadVersionState(db, versionId);
    // A version merged since `target` was resolved: resolve and plan again.
    if (!state) {
      if (typeof target === 'string' || attempt >= attempts) throw new Error('version_not_found');
      continue;
    }
    pending.change?.(state);
    const plan = await recomputeStatements(db, state, options);
    try {
      await db.batch([guard, ...(pending.extra?.(plan) ?? []), ...plan.statements]);
      return plan;
    } catch (error) {
      if (attempt >= attempts || !isCatalogWriteConflict(error)) throw error;
    }
  }
}
