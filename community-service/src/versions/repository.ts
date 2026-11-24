import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { deriveVersionState, type AcceptanceRow, type ExistingVersion } from './acceptance';
import type { EvidenceKeyType, EvidenceTier, StoredKey } from './keys';
import { workWriteGuard } from './works';

export interface VersionRow {
  version_id: string; work_id: string; alias_id: string | null; label_text: string | null;
  status: 'confirmed' | 'candidate' | 'merged_into'; merged_into: string | null; runtime_ms: number | null;
  trust: number; revision: number; quarantined: number; created_at: number;
}

/** Generate an opaque service ID such as `v_<32 hex>`. */
export function serviceId(prefix: string): string {
  return `${prefix}_${[...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('')}`;
}

export async function readVersion(db: SqlDatabase, versionId: string): Promise<VersionRow | null> {
  return db.prepare('SELECT * FROM versions WHERE version_id = ?').bind(versionId).first<VersionRow>();
}

/** Non-merged versions of a work with their tier 1–3 keys, for D13 decisions. */
export async function workVersions(db: SqlDatabase, workId: string): Promise<ExistingVersion[]> {
  const [versions, keys] = await db.batch<Record<string, unknown>>([
    db.prepare(`SELECT version_id, alias_id, label_text, status, trust FROM versions
      WHERE work_id = ? AND status != 'merged_into' ORDER BY version_id`).bind(workId),
    db.prepare(`SELECT k.version_id, k.type, k.algorithm, k.value, k.size_bytes FROM version_match_keys k
      JOIN versions v USING (version_id) WHERE v.work_id = ? AND v.status != 'merged_into'`).bind(workId),
  ]);
  const byVersion = new Map<string, StoredKey[]>();
  for (const key of keys.results) {
    const list = byVersion.get(key.version_id as string) ?? [];
    list.push({ type: key.type as EvidenceKeyType, algorithm: key.algorithm as string, value: key.value as string,
      sizeBytes: key.size_bytes as number });
    byVersion.set(key.version_id as string, list);
  }
  return versions.results.map(row => ({ versionId: row.version_id as string,
    status: row.status as ExistingVersion['status'], trust: row.trust as number,
    label: { aliasId: row.alias_id as string | null, labelText: row.label_text as string | null },
    keys: byVersion.get(row.version_id as string) ?? [] }));
}

export async function acceptanceRows(db: SqlDatabase, versionIds: readonly string[]): Promise<AcceptanceRow[]> {
  const rows = await db.prepare(`SELECT submission_id, contributor_id, accepted_seq, tier, duration_ms, quarantined
    FROM version_acceptances WHERE version_id IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(versionIds))
    .all<{ submission_id: string; contributor_id: string; accepted_seq: number; tier: EvidenceTier | null; duration_ms: number; quarantined: number }>();
  return rows.results.map(row => ({ submissionId: row.submission_id, contributorId: row.contributor_id, acceptedSeq: row.accepted_seq, tier: row.tier,
    durationMs: row.duration_ms, quarantined: row.quarantined === 1 }));
}

/** Statements that write derived runtime, acceptance-based trust and any promotion for one version. */
export function derivedStatements(db: SqlDatabase, versionId: string, rows: readonly AcceptanceRow[], status: string,
  promotionContributors: number): { promoted: boolean; statements: SqlStatement[] } {
  const derived = deriveVersionState(rows, status, promotionContributors);
  const statements = [db.prepare('UPDATE versions SET runtime_ms = ?, trust = ? WHERE version_id = ?')
    .bind(derived.runtimeMs, derived.trust, versionId)];
  if (derived.promote) statements.push(db.prepare(`UPDATE versions SET status = 'confirmed'
    WHERE version_id = ? AND status = 'candidate'`).bind(versionId));
  return { promoted: derived.promote, statements };
}

/**
 * Recompute runtime, trust and promotion of a version from its stored acceptances, for
 * example after a policy change. The statements join the caller's batch and start with a
 * work write guard; retry when the batch fails with a catalog write conflict.
 */
export async function refreshVersionStatements(db: SqlDatabase, versionId: string,
  promotionContributors: number): Promise<SqlStatement[]> {
  const version = await readVersion(db, versionId);
  if (!version) throw new Error('version_not_found');
  const guard = await workWriteGuard(db, version.work_id);
  return [guard, ...derivedStatements(db, versionId, await acceptanceRows(db, [versionId]), version.status,
    promotionContributors).statements];
}

/**
 * Quarantine or restore one accepted submission and recompute its version in the same batch.
 * The previous submission of the same contributor counts again while the latest is quarantined.
 */
export async function acceptanceQuarantineStatements(db: SqlDatabase, submissionId: string, quarantined: boolean,
  promotionContributors: number): Promise<SqlStatement[]> {
  const row = await db.prepare(`SELECT a.version_id, v.work_id, v.status FROM version_acceptances a JOIN versions v USING (version_id)
    WHERE a.submission_id = ?`).bind(submissionId).first<{ version_id: string; work_id: string; status: string }>();
  if (!row) throw new Error('submission_not_found');
  const guard = await workWriteGuard(db, row.work_id);
  const rows = (await acceptanceRows(db, [row.version_id]))
    .map(item => item.submissionId === submissionId ? { ...item, quarantined } : item);
  return [guard, db.prepare('UPDATE version_acceptances SET quarantined = ? WHERE submission_id = ?').bind(Number(quarantined), submissionId),
    ...derivedStatements(db, row.version_id, rows, row.status, promotionContributors).statements];
}
