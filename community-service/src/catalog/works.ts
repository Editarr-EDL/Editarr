import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { type EpisodeNumber, type Identifier } from './compatibility-inputs';
import { identifier, sha256, timestamp, token } from './records';

export async function workStatements(db: SqlDatabase, input: Identifier, now: number) {
  const item = identifier(input);
  timestamp(now);
  const existing = await db.prepare(`SELECT work_id FROM work_identifiers
    WHERE provider = ? AND entity_type = ? AND value = ?`).bind(item.provider, item.entity_type, item.value)
    .first<{ work_id: string }>();
  if (existing) return { workId: existing.work_id, statements: [] as SqlStatement[] };
  const workId = `${item.provider}:${item.entity_type}:${item.value}`;
  return { workId, statements: [
    db.prepare(`INSERT INTO works (work_id, entity_type, created_at) VALUES (?, ?, ?)
      ON CONFLICT(work_id) DO NOTHING`).bind(workId, item.entity_type, now),
    db.prepare(`INSERT INTO work_identifiers
      (provider, entity_type, value, work_id, provenance, evidence_ref)
      VALUES (?, ?, ?, ?, 'user_asserted', 'initial_association')
      ON CONFLICT(provider, entity_type, value) DO NOTHING`).bind(item.provider, item.entity_type, item.value, workId),
  ] };
}

export async function ensureWork(db: SqlDatabase, item: Identifier, now: number): Promise<string> {
  const plan = await workStatements(db, item, now);
  if (plan.statements.length) await db.batch(plan.statements);
  return plan.workId;
}

export async function episodeWorkStatements(db: SqlDatabase, seriesWorkId: string,
  number: EpisodeNumber, now: number) {
  const identity = JSON.stringify([seriesWorkId, number.scheme, number.season, number.number]);
  const workId = `episode:${await sha256(new TextEncoder().encode(identity))}`;
  return { workId, statements: [db.prepare(`INSERT INTO works
    (work_id, entity_type, created_at) VALUES (?, 'episode', ?)
    ON CONFLICT(work_id) DO NOTHING`).bind(workId, timestamp(now))] };
}

export async function addVerifiedIdentifier(db: SqlDatabase, workId: string, input: Identifier,
  proof: { provenance: 'verified_source' | 'operator'; evidenceRef: string }): Promise<void> {
  const item = identifier(input);
  if (!['verified_source', 'operator'].includes(proof.provenance)) throw new Error('unverified_work_alias');
  await db.prepare(`INSERT INTO work_identifiers
    (provider, entity_type, value, work_id, provenance, evidence_ref) VALUES (?, ?, ?, ?, ?, ?)`)
    .bind(item.provider, item.entity_type, item.value, workId, proof.provenance, token(proof.evidenceRef)).run();
}