import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { identifierRow, type IdentifierRow } from './keys';
import { normalizedTitle } from './normalize';

/** The `work` object of a submission artifact (§5.2). */
export interface SubmissionWork {
  kind: 'movie' | 'episode'; tmdb: string | null; tvdb: string | null;
  season: number | null; episode: number | null; lastEpisode: number | null; title: string; year: number | null;
}
export interface WorkPlan {
  workId: string; created: boolean; conflicts: number; statements: SqlStatement[];
  /** Identifiers this plan attaches and its title observation, for summaries computed before commit. */
  attached: IdentifierRow[]; title: { normalized: string; year: number; title: string };
}

export class VersionAcceptanceError extends Error {
  constructor(public code: 'work_identity_required' | 'unknown_alias' | 'invalid_submission') { super(code); }
}

export function workIdentifiers(work: SubmissionWork): IdentifierRow[] {
  return (['tmdb', 'tvdb'] as const).flatMap(provider => work[provider] === null ? [] :
    [identifierRow(provider, work.kind, work[provider]!, work.season, work.episode, work.lastEpisode)]);
}

export async function identifiedWork(db: SqlDatabase, row: IdentifierRow): Promise<string | null> {
  return (await db.prepare(`SELECT work_id FROM work_identifiers WHERE provider = ? AND kind = ? AND value = ?
    AND season = ? AND episode = ? AND last_episode = ?`)
    .bind(row.provider, row.kind, row.value, row.season, row.episode, row.lastEpisode).first<{ work_id: string }>())?.work_id ?? null;
}

/** Errors that mean a concurrent catalog write won; re-plan and retry the operation. */
export function isCatalogWriteConflict(error: unknown): boolean {
  return /catalog_write_conflict|version_label_conflict|UNIQUE constraint failed: (?:work_identifiers|versions\.created_seq|version_acceptances\.accepted_seq|version_match_keys)/
    .test(String(error));
}

/**
 * Optimistic write guard for one work. Every catalog write that depends on a work's versions
 * (acceptance, confirm, merge, relabel, quarantine) advances `catalog_seq` from the value it
 * read; a concurrent writer makes the batch abort with `catalog_write_conflict`.
 */
export async function workWriteGuard(db: SqlDatabase, workId: string): Promise<SqlStatement> {
  const row = await db.prepare('SELECT catalog_seq FROM works WHERE work_id = ?').bind(workId).first<{ catalog_seq: number }>();
  if (!row) throw new Error('work_not_found');
  return db.prepare('UPDATE works SET catalog_seq = ? WHERE work_id = ?').bind(row.catalog_seq + 1, workId);
}

/**
 * §3.5 step 1: find the work by any provider identifier or create it. When TMDB and TVDB
 * resolve to different works the TMDB work wins and the TVDB identifier is recorded as a
 * conflict instead of attached. New identifiers and the title/year key are attached.
 */
export async function planWork(db: SqlDatabase, work: SubmissionWork, submissionId: string, now: number,
  newId: (prefix: string) => string): Promise<WorkPlan> {
  const title = normalizedTitle(work.title);
  const rows = workIdentifiers(work);
  if (!rows.length) throw new VersionAcceptanceError('work_identity_required');
  if (title === null || (work.kind === 'episode') !== (work.season !== null && work.episode !== null))
    throw new VersionAcceptanceError('invalid_submission');
  const found = await Promise.all(rows.map(row => identifiedWork(db, row)));
  const existing = found.find(workId => workId !== null) ?? null;
  const workId = existing ?? newId('w');
  const statements: SqlStatement[] = [existing ? await workWriteGuard(db, workId)
    : db.prepare('INSERT INTO works (work_id, kind, catalog_seq, created_at) VALUES (?, ?, 0, ?)').bind(workId, work.kind, now)];
  let conflicts = 0;
  const attached: IdentifierRow[] = [];
  rows.forEach((row, index) => {
    if (found[index] === workId) return;
    if (found[index] !== null) {
      conflicts++;
      statements.push(db.prepare(`INSERT INTO work_identifier_conflicts
        (conflict_id, submission_id, provider, value, existing_work_id, created_at) VALUES (?, ?, ?, ?, ?, ?)`)
        .bind(newId('wc'), submissionId, row.provider, row.value, found[index], now));
      return;
    }
    attached.push(row);
    statements.push(db.prepare(`INSERT INTO work_identifiers
      (provider, kind, value, season, episode, last_episode, work_id) VALUES (?, ?, ?, ?, ?, ?, ?)`)
      .bind(row.provider, row.kind, row.value, row.season, row.episode, row.lastEpisode, workId));
  });
  statements.push(db.prepare(`INSERT INTO work_titles (work_id, normalized, year, title, observations) VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(work_id, normalized, year) DO UPDATE SET observations = observations + 1`)
    .bind(workId, title, work.year ?? -1, work.title));
  return { workId, created: !existing, conflicts, statements, attached,
    title: { normalized: title, year: work.year ?? -1, title: work.title } };
}
