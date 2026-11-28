import type { SqlDatabase } from '../../runtime/contracts';
import { z } from 'zod';
import type { ContributorLabel } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, InsightQueryError, ms, parseQuery, PERIOD_SECONDS } from './query';

export const LEADERBOARD_METRICS = ['accepted', 'uploads', 'votes_received', 'votes_cast'] as const;
export type LeaderMetric = typeof LEADERBOARD_METRICS[number];
const LEADERBOARD_PAGE = 50;

/**
 * One leaderboard row. `accepted` counts distinct served, unquarantined master filters that the
 * contributor's counted support attestations (from uploads in the period) back — contract decision 3.
 * `votesReceived` is the net of others' current votes, cast in the period, on filters the contributor
 * supports; `votesCast` counts the contributor's current votes cast in the period; `reportsAgainst`
 * counts reports filed in the period on filters the contributor supports.
 */
export interface LeaderRow {
  contributor: ContributorLabel; rank: number; accepted: number; uploads: number; votesReceived: number;
  votesCast: number; reportsAgainst: number; joinedAt: number; state: string;
}

const order: Record<LeaderMetric, string> = {
  accepted: 'accepted DESC, uploads DESC', uploads: 'uploads DESC, accepted DESC',
  votes_received: 'votesReceived DESC, accepted DESC', votes_cast: 'votesCast DESC, accepted DESC',
};

/**
 * Rank contributors with any activity since `since` (Unix seconds; 0 for all time) by `metric`,
 * ties broken by the secondary metric then contributor ID. Returns `limit` rows from `offset` and the total.
 */
export async function readLeaderboard(db: SqlDatabase, since: number, metric: LeaderMetric, offset: number, limit: number) {
  const supported = (bounded: boolean) => `SELECT DISTINCT a.contributor_id AS c, a.filter_id AS f FROM filter_attestations a
    JOIN master_filters m ON m.filter_id = a.filter_id AND m.served = 1
    JOIN filter_registry r ON r.filter_id = a.filter_id AND r.quarantined = 0
    ${bounded ? 'JOIN version_acceptances va ON va.submission_id = a.submission_id AND va.accepted_at >= ?1' : ''}
    WHERE a.counted = 1 AND a.kind = 'support'`;
  const rows = await db.prepare(`WITH supported AS (${supported(false)}), accepted AS (${supported(true)}),
    scores AS (SELECT c.contributor_id AS contributorId, c.display_name AS displayName, c.created_at AS joinedAt, c.state,
      (SELECT count(*) FROM accepted WHERE accepted.c = c.contributor_id) AS accepted,
      (SELECT count(*) FROM version_acceptances WHERE contributor_id = c.contributor_id AND accepted_at >= ?1) AS uploads,
      coalesce((SELECT sum(CASE v.vote WHEN 'up' THEN 1 ELSE -1 END) FROM supported s JOIN filter_votes v ON v.filter_id = s.f
        WHERE s.c = c.contributor_id AND v.excluded = 0 AND v.contributor_id != s.c AND v.updated_at >= ?1), 0) AS votesReceived,
      (SELECT count(*) FROM filter_votes WHERE contributor_id = c.contributor_id AND excluded = 0 AND updated_at >= ?1) AS votesCast,
      (SELECT count(*) FROM supported s JOIN reports r ON r.target_type = 'filter' AND r.target_id = s.f
        WHERE s.c = c.contributor_id AND r.created_at >= ?1) AS reportsAgainst
      FROM contributors c)
    SELECT *, count(*) OVER () AS total FROM scores WHERE accepted + uploads + votesCast + votesReceived != 0
    ORDER BY ${order[metric]}, contributorId LIMIT ?2 OFFSET ?3`).bind(since, limit, offset).all<Record<string, any>>();
  const items = rows.results.map((row, index): LeaderRow => ({
    contributor: { contributorId: row.contributorId, displayName: row.displayName }, rank: offset + index + 1,
    accepted: row.accepted, uploads: row.uploads, votesReceived: row.votesReceived, votesCast: row.votesCast,
    reportsAgainst: row.reportsAgainst, joinedAt: ms(row.joinedAt)!, state: row.state }));
  return { items, total: Number(rows.results[0]?.total ?? 0) };
}

/** `GET /admin/v1/contributors/leaderboard?period=7d|30d|all&metric=&after=` (50 rows per page). */
export async function leaderboardPage(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_leaderboard_query';
  const query = parseQuery(url, code, { period: z.enum(['7d', '30d', 'all']).optional(),
    metric: z.enum(LEADERBOARD_METRICS).optional(), after: cursorParam.optional() });
  const offset = Number(decodeCursor(query.after, ['n'], code)?.[0] ?? 0);
  if (offset < 0 || offset > 10_000) throw new InsightQueryError(code);
  const period = query.period ?? '30d', metric = query.metric ?? 'accepted';
  const since = period === 'all' ? 0 : now - PERIOD_SECONDS[period];
  const { items, total } = await readLeaderboard(db, since, metric, offset, LEADERBOARD_PAGE);
  return { version: 1, observedAt: now * 1000, period, metric, since: since * 1000, total, items,
    nextCursor: offset + items.length < total ? encodeCursor([offset + items.length]) : null };
}
