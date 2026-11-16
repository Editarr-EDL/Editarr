import type { SqlDatabase } from '../runtime/contracts';
import { type verifyArtifactSignature } from '../artifact/signatures';
import type { ResourceBounds } from '../artifact/codec';
import { matchEvidenceSchema } from '../catalog/compatibility-inputs';
import { boundedJson, positive, sha256, SQL_BIND_LIMIT, timestamp, token } from '../catalog/records';
import { releaseStatements } from '../catalog/releases';
import { validateParentPermission } from './permission';

type VerifiedArtifact = Awaited<ReturnType<typeof verifyArtifactSignature>>;
export interface CatalogLimits {
  maxParents: number; maxLineageDepth: number; maxJsonBytes: number; maxReadQueries: number; maxStatements: number;
}
export interface ReservationInput {
  verified: VerifiedArtifact; contributionId: string; releaseId: string; now: number; limits: CatalogLimits;
  operation: { kind: string; id: string };
  permissionBounds?: ResourceBounds;
}

export async function revisionStatements(db: SqlDatabase, input: ReservationInput) {
  const { verified, limits } = input;
  positive(limits.maxParents); positive(limits.maxLineageDepth); positive(limits.maxJsonBytes);
  token(input.contributionId); token(input.releaseId); timestamp(input.now);
  token(input.operation.kind); token(input.operation.id);
  const artifact = verified.artifact.artifact;
  const revisionId = token(artifact.submission_id);
  if (artifact.author.contributor_id !== verified.contributorId || artifact.author.key_id !== verified.keyId)
    throw new Error('revision_author_mismatch');
  const legacyEntity = artifact.media.work_ids.find(item => item.entity_type !== 'series')?.entity_type ?? 'series';
  const evidence = artifact.schema_version === 'community-artifact-2' ? artifact.matching :
    matchEvidenceSchema.parse({ media: { entity_type: legacyEntity,
      identifiers: artifact.media.work_ids.filter(item => item.entity_type === legacyEntity).map(item => ({ ...item, provenance: 'signed_claim' })),
      series_identifiers: legacyEntity === 'episode' ? artifact.media.work_ids.filter(item => item.entity_type === 'series').map(item => ({ ...item, provenance: 'signed_claim' })) : [],
      release: { scene_name: artifact.media.release ?? null } } });
  const parents = new Map<string, string>();
  for (const parent of [...artifact.source_references ?? [], ...artifact.derivation?.parents ?? []]) {
    token(parent.submission_id);
    if (parents.has(parent.submission_id) && parents.get(parent.submission_id) !== parent.digest)
      throw new Error('conflicting_parent_digest');
    parents.set(parent.submission_id, parent.digest);
  }
  if (parents.size > limits.maxParents) throw new Error('parent_limit');
  const readQueries = evidence.media.identifiers.length + evidence.media.series_identifiers.length + Math.ceil(parents.size / SQL_BIND_LIMIT);
  if (readQueries > positive(limits.maxReadQueries)) throw new Error('catalog_read_limit');
  let depth = 0;
  const parentIds = [...parents.keys()];
  for (let offset = 0; offset < parentIds.length; offset += SQL_BIND_LIMIT) {
    const chunk = parentIds.slice(offset, offset + SQL_BIND_LIMIT);
    const rows = await db.prepare(`SELECT r.revision_id, r.artifact_digest, r.lineage_depth, p.state
      ${input.permissionBounds ? ', r.contributor_id, payload.artifact' : ''}
      FROM contribution_revisions r JOIN contribution_publications p USING (revision_id)
      ${input.permissionBounds ? 'LEFT JOIN artifact_payloads payload ON payload.object_key = r.object_key' : ''}
      WHERE r.revision_id IN (${chunk.map(() => '?').join(',')})`).bind(...chunk)
      .all<{ revision_id: string; artifact_digest: string; lineage_depth: number; state: string;
        contributor_id: string; artifact: ArrayBuffer | null }>();
    if (rows.results.length !== chunk.length) throw new Error('missing_parent');
    for (const row of rows.results) {
      if (row.state !== 'published' || row.artifact_digest !== parents.get(row.revision_id)) throw new Error('invalid_parent');
      if (input.permissionBounds) await validateParentPermission(artifact, row, input.permissionBounds);
      depth = Math.max(depth, row.lineage_depth + 1);
    }
  }
  if (depth > limits.maxLineageDepth) throw new Error('lineage_depth_limit');
  const release = await releaseStatements(db, input.releaseId, evidence, input.now, limits);
  const summary = verified.artifact.summary;
  const objectKey = `artifacts/${crypto.randomUUID()}`;
  const digests = await Promise.all([verified.artifactBytes, verified.manifestBytes, verified.envelopeBytes].map(sha256));
  const statements = [...release.statements,
    db.prepare(`INSERT INTO contributions (contribution_id, contributor_id, created_at) VALUES (?, ?, ?)
      ON CONFLICT(contribution_id) DO NOTHING`).bind(input.contributionId, verified.contributorId, input.now),
    db.prepare(`INSERT INTO contribution_revisions (revision_id, contribution_id, contributor_id,
      operation_kind, operation_id,
      key_id, release_id, kind, artifact_schema, artifact_digest, manifest_digest, envelope_digest,
      artifact_bytes, manifest_bytes, envelope_bytes, object_key, summary_json, total_events, audio_events, video_events, lineage_depth, parent_limit, supersedes, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(revisionId,
      input.contributionId, verified.contributorId, input.operation.kind, input.operation.id,
      verified.keyId, input.releaseId, artifact.kind,
      artifact.schema_version, ...digests, verified.artifactBytes.byteLength, verified.manifestBytes.byteLength,
      verified.envelopeBytes.byteLength, objectKey, boundedJson(summary, limits.maxJsonBytes),
      summary.total, summary.channels.audio ?? 0, summary.channels.video ?? 0, depth, limits.maxParents, artifact.supersedes ?? null, input.now),
    db.prepare(`INSERT INTO contribution_publications (revision_id, state, updated_at)
      VALUES (?, 'reserved', ?)`).bind(revisionId, input.now),
    db.prepare(`INSERT INTO contribution_statistics (revision_id) VALUES (?)`).bind(revisionId),
  ];
  for (const [parentId, digest] of parents) statements.push(db.prepare(`INSERT INTO contribution_parents
    (revision_id, parent_revision_id, parent_digest) VALUES (?, ?, ?)`).bind(revisionId, parentId, digest));
  if (statements.length > positive(limits.maxStatements)) throw new Error('catalog_statement_limit');
  return { revisionId, objectKey, releaseId: input.releaseId, workId: release.workId, readQueries, statements };
}

export async function reserveRevision(db: SqlDatabase, input: ReservationInput) {
  const plan = await revisionStatements(db, input);
  await db.batch(plan.statements);
  return { revisionId: plan.revisionId, objectKey: plan.objectKey, releaseId: plan.releaseId, workId: plan.workId };
}

export async function readReservation(db: SqlDatabase, contributorId: string, operation: { kind: string; id: string }) {
  token(contributorId); token(operation.kind); token(operation.id);
  return db.prepare(`SELECT r.*, p.state, p.object_etag, p.updated_at FROM contribution_revisions r
    JOIN contribution_publications p USING (revision_id)
    WHERE r.contributor_id = ? AND r.operation_kind = ? AND r.operation_id = ?`)
    .bind(contributorId, operation.kind, operation.id).first<{
      revision_id: string; object_key: string; state: string; artifact_digest: string;
      manifest_digest: string; envelope_digest: string; artifact_bytes: number;
      manifest_bytes: number; envelope_bytes: number; object_etag: string | null;
      key_id: string; contribution_id: string;
    }>();
}

export function publicationStatement(db: SqlDatabase, input: {
  revisionId: string; from: 'reserved' | 'object_stored' | 'published' | 'withdrawn' | 'quarantined';
  to: 'object_stored' | 'published' | 'failed' | 'expired' | 'withdrawn' | 'quarantined';
  now: number; objectEtag?: string;
}) {
  token(input.revisionId); timestamp(input.now);
  if (input.to === 'object_stored' && (!input.objectEtag || input.objectEtag.length > 256))
    throw new Error('missing_object_confirmation');
  return db.prepare(`UPDATE contribution_publications
    SET state = ?, object_etag = coalesce(object_etag, ?), updated_at = ?
    WHERE revision_id = ? AND state = ? RETURNING revision_id`).bind(input.to, input.objectEtag ?? null, input.now, input.revisionId, input.from);
}

export async function transitionPublication(db: SqlDatabase, input: Parameters<typeof publicationStatement>[1]) {
  const result = await publicationStatement(db, input).all();
  if (result.results.length !== 1) throw new Error('publication_conflict');
}