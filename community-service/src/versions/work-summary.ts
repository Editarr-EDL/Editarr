import type { SqlDatabase } from '../runtime/contracts';
import type { IdentifierRow } from './keys';

/** The `work` object served by match results and master snapshots (§7.1, §7.3). */
export interface WorkSummary {
  workId: string; kind: 'movie' | 'episode'; tmdb: string | null; tvdb: string | null;
  season: number | null; episode: number | null; lastEpisode: number | null; title: string; year: number | null;
}
/** Stored rows behind work summaries; numbering and year use -1 for null, as in the tables. */
export interface WorkRows {
  works: { workId: string; kind: WorkSummary['kind'] }[];
  identifiers: { workId: string; provider: 'tmdb' | 'tvdb'; value: string; season: number; episode: number; lastEpisode: number }[];
  titles: { workId: string; normalized: string; title: string; year: number; observations: number }[];
}

const nullable = (value: number) => value === -1 ? null : value;
const encoder = new TextEncoder();
/** SQLite BINARY collation: byte order of UTF-8. */
const binary = (a: string, b: string) => Buffer.compare(encoder.encode(a), encoder.encode(b));

export async function readWorkRows(db: SqlDatabase, workIds: readonly string[]): Promise<WorkRows> {
  const ids = JSON.stringify([...new Set(workIds)]);
  const [works, identifiers, titles] = await db.batch<Record<string, unknown>>([
    db.prepare('SELECT work_id AS workId, kind FROM works WHERE work_id IN (SELECT value FROM json_each(?))').bind(ids),
    db.prepare(`SELECT work_id AS workId, provider, value, season, episode, last_episode AS lastEpisode FROM work_identifiers
      WHERE work_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT work_id AS workId, normalized, title, year, observations FROM work_titles
      WHERE work_id IN (SELECT value FROM json_each(?))`).bind(ids),
  ]);
  return { works: works.results as WorkRows['works'], identifiers: identifiers.results as WorkRows['identifiers'],
    titles: titles.results as WorkRows['titles'] };
}

/**
 * Add one pending acceptance's work writes (a new work, attached identifiers and a title
 * observation) to stored rows, so a summary can be computed before the batch commits.
 */
export function withPendingWork(rows: WorkRows, workId: string, kind: WorkSummary['kind'], attached: readonly IdentifierRow[],
  title: { normalized: string; year: number; title: string }): WorkRows {
  const titles = rows.titles.map(row => ({ ...row }));
  const current = titles.find(row => row.workId === workId && row.normalized === title.normalized && row.year === title.year);
  if (current) current.observations++;
  else titles.push({ workId, ...title, observations: 1 });
  return { works: rows.works.some(row => row.workId === workId) ? rows.works : [...rows.works, { workId, kind }],
    identifiers: [...rows.identifiers, ...attached.map(row => ({ workId, provider: row.provider, value: row.value,
      season: row.season, episode: row.episode, lastEpisode: row.lastEpisode }))], titles };
}

/**
 * Summarize works. Each provider uses its smallest identifier; numbering comes from the TMDB
 * identifier when present. The title is the most observed title key, then binary title and year order.
 */
export function summarizeWorks(rows: WorkRows): Map<string, WorkSummary> {
  const summaries = new Map<string, WorkSummary>();
  for (const row of rows.works) summaries.set(row.workId, { workId: row.workId, kind: row.kind, tmdb: null, tvdb: null,
    season: null, episode: null, lastEpisode: null, title: '', year: null });
  const identifiers = [...rows.identifiers].sort((a, b) => binary(a.provider, b.provider) || a.value.length - b.value.length ||
    binary(a.value, b.value) || a.season - b.season || a.episode - b.episode || a.lastEpisode - b.lastEpisode);
  for (const row of identifiers) {
    const work = summaries.get(row.workId);
    if (!work || work[row.provider] !== null) continue;
    work[row.provider] = row.value;
    if (row.provider === 'tmdb' || work.tmdb === null) Object.assign(work, { season: nullable(row.season),
      episode: nullable(row.episode), lastEpisode: nullable(row.lastEpisode) });
  }
  const titles = [...rows.titles].sort((a, b) => b.observations - a.observations || binary(a.title, b.title) || a.year - b.year);
  for (const row of titles) {
    const work = summaries.get(row.workId);
    if (!work || work.title) continue;
    work.title = row.title;
    work.year = nullable(row.year);
  }
  return summaries;
}

export async function workSummaries(db: SqlDatabase, workIds: readonly string[]): Promise<Map<string, WorkSummary>> {
  return summarizeWorks(await readWorkRows(db, workIds));
}
