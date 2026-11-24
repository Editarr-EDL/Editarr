import type { SqlDatabase } from '../runtime/contracts';

/** The `work` object served by match results and master snapshots (§7.1, §7.3). */
export interface WorkSummary {
  workId: string; kind: 'movie' | 'episode'; tmdb: string | null; tvdb: string | null;
  season: number | null; episode: number | null; lastEpisode: number | null; title: string; year: number | null;
}

const nullable = (value: number) => value === -1 ? null : value;

/**
 * Summarize works by ID. Each provider uses its smallest identifier; numbering comes from the
 * TMDB identifier when present. The title is the most observed title key, then ASCII order.
 */
export async function workSummaries(db: SqlDatabase, workIds: readonly string[]): Promise<Map<string, WorkSummary>> {
  const ids = JSON.stringify([...new Set(workIds)]);
  const [works, identifiers, titles] = await db.batch<Record<string, unknown>>([
    db.prepare('SELECT work_id, kind FROM master_works WHERE work_id IN (SELECT value FROM json_each(?))').bind(ids),
    db.prepare(`SELECT work_id, provider, value, season, episode, last_episode FROM master_work_identifiers
      WHERE work_id IN (SELECT value FROM json_each(?)) ORDER BY provider, length(value), value, season, episode, last_episode`).bind(ids),
    db.prepare(`SELECT work_id, title, year FROM work_titles WHERE work_id IN (SELECT value FROM json_each(?))
      ORDER BY observations DESC, title, year`).bind(ids),
  ]);
  const summaries = new Map<string, WorkSummary>();
  for (const row of works.results) summaries.set(row.work_id as string, { workId: row.work_id as string,
    kind: row.kind as WorkSummary['kind'], tmdb: null, tvdb: null, season: null, episode: null, lastEpisode: null,
    title: '', year: null });
  for (const row of identifiers.results) {
    const work = summaries.get(row.work_id as string)!, provider = row.provider as 'tmdb' | 'tvdb';
    if (work[provider] !== null) continue;
    work[provider] = row.value as string;
    if (provider === 'tmdb' || work.tmdb === null) Object.assign(work, { season: nullable(row.season as number),
      episode: nullable(row.episode as number), lastEpisode: nullable(row.last_episode as number) });
  }
  for (const row of titles.results) {
    const work = summaries.get(row.work_id as string)!;
    if (work.title) continue;
    work.title = row.title as string;
    work.year = nullable(row.year as number);
  }
  return summaries;
}
