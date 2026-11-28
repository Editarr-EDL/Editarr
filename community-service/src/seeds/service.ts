import { DEFAULT_MASTER_POLICY, parseMasterPolicy } from '../filters/policy';
import { archiveSeed, registerSource, sha256, sourceSigner } from './archive';
import { seedBundle } from './input';
import { recomputeStatements, type RecomputeOptions } from '../master/recompute';
import type { VersionState } from '../master/state';
import type { SqlDatabase } from '../runtime/contracts';
import { submissionInput, submissionStatements } from '../submissions/store';
import { serviceId } from '../versions/repository';
import { readWorkRows, withPendingWork } from '../versions/work-summary';
import { planWork } from '../versions/works';

/** Import one archive offline. Keep each source file on its own candidate timeline. */
export async function importExternalSeed(db: SqlDatabase, input: unknown, options: RecomputeOptions) {
  const { source, records } = seedBundle.parse(input);
  const totals = { source, inserted: 0, unchanged: 0, filters: 0, duplicates: 0 };
  const signer = await sourceSigner(source);
  let registered = false;
  for (const record of records) {
    const inputDigest = sha256(JSON.stringify(record));
    const existing = await db.prepare(`SELECT input_digest FROM external_seed_submissions WHERE source = ? AND source_record_id = ?`)
      .bind(source, record.sourceRecordId).first<{ input_digest: string }>();
    if (existing) {
      if (existing.input_digest !== inputDigest) throw new Error('changed_external_seed_record: ' + record.sourceRecordId);
      totals.unchanged++;
      continue;
    }
    const recordKey = sha256(record.sourceRecordId);
    const submissionId = 'seed_' + recordKey, versionId = 'v_seed_' + recordKey;
    const stored = await archiveSeed(record, source, signer, submissionId, versionId, options.now);
    const work = await planWork(db, stored.artifact.work, submissionId, options.now, serviceId);
    if (work.conflicts) throw new Error('external_seed_identifier_conflict: ' + record.sourceRecordId);
    const seq = (await db.prepare('SELECT coalesce(max(accepted_seq), 0) + 1 AS next FROM version_acceptances')
      .first<{ next: number }>())!.next;
    const state: VersionState = {
      versionId, workId: work.workId, status: 'candidate', aliasId: null, labelText: source, quarantined: false, revision: 1,
      latest: null, workRows: withPendingWork(work.created ? { works: [], identifiers: [], titles: [] }
        : await readWorkRows(db, [work.workId]), work.workId, record.work.kind, work.attached, work.title),
      labels: new Map(), referenceKeys: [],
      submissions: [{ ...submissionInput(stored, seq), durationMs: record.sourceRuntimeMs, externalSeed: true }],
      votes: [], registry: [], quarantinedContributors: [], quarantinedSubmissions: [], quarantinedAttestations: [],
      sourceVersionIds: [versionId],
    };
    const plan = await recomputeStatements(db, state, options);
    const provenance = { ...record.provenance, sourceCueIndices: record.filters.map(filter => filter.sourceCueIndex) };
    if (!registered) {
      await registerSource(db, source, signer, options.now);
      registered = true;
    }
    await db.batch([
      ...work.statements,
      db.prepare(`INSERT INTO versions (version_id, work_id, label_text, status, runtime_ms, trust, revision,
        reference_submission_id, created_seq, created_at)
        VALUES (?, ?, ?, 'candidate', ?, 0, 1, ?, (SELECT coalesce(max(created_seq), 0) + 1 FROM versions), ?)`)
        .bind(versionId, work.workId, source, record.sourceRuntimeMs, submissionId, options.now),
      db.prepare(`INSERT INTO version_acceptances (submission_id, version_id, contributor_id, accepted_seq, founder, duration_ms, accepted_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)`).bind(submissionId, versionId, signer.contributorId, seq, stored.artifact.media.durationMs, options.now),
      ...submissionStatements(db, stored, options.now),
      db.prepare(`INSERT INTO external_seed_submissions
        (submission_id, source, source_record_id, input_digest, source_runtime_ms, provenance_json) VALUES (?, ?, ?, ?, ?, ?)`)
        .bind(submissionId, source, record.sourceRecordId, inputDigest, record.sourceRuntimeMs, JSON.stringify(provenance)),
      ...plan.statements,
    ]);
    totals.inserted++;
    totals.filters += plan.result.filters.length;
    totals.duplicates += record.filters.length - plan.result.filters.length;
  }
  return totals;
}

export function seedOptions(service: string, policy: string | undefined): RecomputeOptions {
  const masterPolicy = policy ? parseMasterPolicy(JSON.parse(policy)) : DEFAULT_MASTER_POLICY;
  return { service, policy: masterPolicy, now: Math.floor(Date.now() / 1000) };
}
