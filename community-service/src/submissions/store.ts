import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { ArtifactV3 } from '../artifact/v3';
import type { SubmissionInput } from '../filters';

export interface StoredSubmission {
  submissionId: string; digest: string; contributorId: string; installationId: string; keyId: string; versionId: string;
  tier: 1 | 2 | 3 | null; artifact: ArtifactV3; artifactBytes: Uint8Array; manifestBytes: Uint8Array; envelopeBytes: Uint8Array;
}

/** The artifact's tier 1–3 keys as stored in `submissions.match_keys_json` and read by the merge (§6.2). */
export function artifactMatchKeys(artifact: ArtifactV3) {
  return artifact.media.matchKeys.map(key => 'algorithm' in key
    ? { type: key.type, algorithm: key.algorithm, value: key.value } : { type: key.type, value: key.value, sizeBytes: key.sizeBytes });
}

/** The merge input of a submission that is not stored yet; its entries have no prior filter IDs. */
export function submissionInput(stored: StoredSubmission, acceptedSeq: number): SubmissionInput {
  const { artifact } = stored;
  return {
    submissionId: stored.submissionId, digest: stored.digest, contributorId: stored.contributorId, acceptedSeq,
    durationMs: artifact.media.durationMs, matchKeys: artifactMatchKeys(artifact),
    coverage: artifact.coverage.map(item => ({ category: item.category, intervals: item.intervals })),
    entries: artifact.entries.map(entry => ({ entryId: entry.entryId, kind: entry.kind, proposedFilterId: entry.proposedFilterId,
      startMs: entry.startMs, endMs: entry.endMs, category: entry.category, subcategory: entry.subcategory,
      severity: entry.severity, action: entry.action, origin: entry.evidence.origin,
      locallyVerified: entry.evidence.locallyVerified, priorFilterId: null })),
  };
}

/**
 * Insert the raw signed buffers once (D3) with the rows derived from the artifact. They follow the
 * acceptance statements, whose `version_acceptances` row supplies the accepted sequence.
 */
export function submissionStatements(db: SqlDatabase, stored: StoredSubmission, now: number): SqlStatement[] {
  const { artifact } = stored;
  return [
    db.prepare(`INSERT INTO submissions (submission_id, digest, contributor_id, installation_id, key_id, version_id, accepted_seq, tier,
        artifact_bytes, manifest_bytes, envelope_bytes, match_keys_json, alignment_hint_json, fit_json, state, created_at)
      SELECT ?, ?, ?, ?, ?, ?, accepted_seq, ?, ?, ?, ?, ?, ?, NULL, 'counted', ? FROM version_acceptances WHERE submission_id = ?`)
      .bind(stored.submissionId, stored.digest, stored.contributorId, stored.installationId, stored.keyId, stored.versionId,
        stored.tier, stored.artifactBytes, stored.manifestBytes, stored.envelopeBytes, JSON.stringify(artifactMatchKeys(artifact)),
        artifact.alignmentHint === null ? null : JSON.stringify(artifact.alignmentHint), now, stored.submissionId),
    db.prepare(`INSERT INTO submission_entries (submission_id, entry_id, kind, proposed_filter_id, start_ms, end_ms, category,
        subcategory, severity, action, origin, manual_review, locally_verified, model_version)
      SELECT ?, value ->> '$.entryId', value ->> '$.kind', value ->> '$.proposedFilterId', value ->> '$.startMs', value ->> '$.endMs',
        value ->> '$.category', value ->> '$.subcategory', value ->> '$.severity', value ->> '$.action',
        value ->> '$.evidence.origin', value ->> '$.evidence.manualReview', value ->> '$.evidence.locallyVerified',
        value ->> '$.evidence.modelVersion' FROM json_each(?)`).bind(stored.submissionId, JSON.stringify(artifact.entries)),
    db.prepare(`INSERT INTO submission_coverage (submission_id, category, ordinal, start_ms, end_ms)
      SELECT ?, category.value ->> '$.category', interval.key, interval.value ->> '$.startMs', interval.value ->> '$.endMs'
      FROM json_each(?) AS category, json_each(category.value, '$.intervals') AS interval`)
      .bind(stored.submissionId, JSON.stringify(artifact.coverage)),
  ];
}
