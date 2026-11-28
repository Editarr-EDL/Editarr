import type { SqlDatabase } from '../../runtime/contracts';
import { z } from 'zod';
import { normalize } from '../../versions/normalize';
import { itemColumns, itemFromRow, versionLabelSql, type ContributorLabel } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, InsightQueryError, ms, PAGE_SIZE, parseQuery } from './query';

export const ITEM_SORTS = ['updated', 'uploads', 'votes', 'reports', 'title'] as const;
type Sort = typeof ITEM_SORTS[number];

/** Per-work aggregates over non-merged versions; `w` is the works alias. */
export const workAggregates = `
  max(w.created_at, coalesce((SELECT max(m.generated_at) FROM versions v JOIN master_revisions m ON m.version_id = v.version_id
    WHERE v.work_id = w.work_id), 0), coalesce((SELECT max(a.accepted_at) FROM versions v
    JOIN version_acceptances a ON a.version_id = v.version_id WHERE v.work_id = w.work_id), 0)) AS lastChangeAt,
  (SELECT count(*) FROM versions v JOIN master_filters f ON f.version_id = v.version_id AND f.served = 1
    WHERE v.work_id = w.work_id AND v.status != 'merged_into') AS filters,
  (SELECT coalesce(sum(f.upvotes - f.downvotes), 0) FROM versions v JOIN master_filters f ON f.version_id = v.version_id
    WHERE v.work_id = w.work_id AND v.status != 'merged_into') AS netVotes,
  (SELECT count(*) FROM versions v JOIN version_acceptances a ON a.version_id = v.version_id WHERE v.work_id = w.work_id) AS uploads,
  (SELECT count(*) FROM reports r WHERE r.state = 'open' AND w.work_id = CASE r.target_type
    WHEN 'version' THEN (SELECT work_id FROM versions WHERE version_id = r.target_id)
    ELSE (SELECT v.work_id FROM filter_registry fr JOIN versions v ON v.version_id = fr.version_id WHERE fr.filter_id = r.target_id) END) AS openReports`;

const sortValue: Record<Sort, string> = {
  updated: 'lastChangeAt', uploads: 'uploads', votes: 'netVotes', reports: 'openReports', title: 'sortTitle',
};

export interface ItemVersion { versionId: string; label: string; status: string; quarantined: boolean; runtimeMs: number | null; revision: number }

/** Versions (non-merged, in creation order) and up to five top uploaders for each work on a page. */
export async function pageDetails(db: SqlDatabase, workIds: string[]) {
  const ids = JSON.stringify(workIds);
  const [versions, contributors] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT v.work_id AS workId, v.version_id AS versionId, ${versionLabelSql('v')} AS label, v.status, v.quarantined,
      v.runtime_ms AS runtimeMs, v.revision FROM versions v WHERE v.work_id IN (SELECT value FROM json_each(?))
      AND v.status != 'merged_into' ORDER BY v.created_seq`).bind(ids),
    db.prepare(`SELECT workId, contributorId, displayName, uploads FROM (SELECT v.work_id AS workId, a.contributor_id AS contributorId,
      coalesce(c.display_name, a.contributor_id) AS displayName, count(*) AS uploads,
      row_number() OVER (PARTITION BY v.work_id ORDER BY count(*) DESC, a.contributor_id) AS position
      FROM versions v JOIN version_acceptances a ON a.version_id = v.version_id LEFT JOIN contributors c ON c.contributor_id = a.contributor_id
      WHERE v.work_id IN (SELECT value FROM json_each(?)) GROUP BY v.work_id, a.contributor_id) WHERE position <= 5
      ORDER BY workId, position`).bind(ids)]);
  const byWork = <T>(rows: Record<string, any>[], map: (row: Record<string, any>) => T) => {
    const result = new Map<string, T[]>();
    for (const row of rows) result.set(row.workId, [...result.get(row.workId) ?? [], map(row)]);
    return result;
  };
  return {
    versions: byWork(versions.results, (row): ItemVersion => ({ versionId: row.versionId, label: row.label, status: row.status,
      quarantined: row.quarantined === 1, runtimeMs: row.runtimeMs, revision: row.revision })),
    contributors: byWork(contributors.results, (row): ContributorLabel => ({ contributorId: row.contributorId, displayName: row.displayName })),
  };
}

/**
 * `GET /admin/v1/items?sort=&q=&after=`: works with aggregates, 20 per page. Numeric sorts are
 * descending and `title` ascending, each tie-broken by work ID; `q` matches normalized titles.
 */
export async function itemsPage(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_items_query';
  const query = parseQuery(url, code, { sort: z.enum(ITEM_SORTS).optional(), q: z.string().min(1).max(100).optional(),
    after: cursorParam.optional() });
  const sort = query.sort ?? 'updated', q = query.q === undefined ? null : normalize(query.q);
  if (q === '') throw new InsightQueryError(code);
  const cursor = decodeCursor(query.after, [sort === 'title' ? 's' : 'n', 's'], code);
  const value = sortValue[sort], ascending = sort === 'title';
  const keyset = cursor ? `AND (${value} ${ascending ? '>' : '<'} ?2 OR (${value} = ?2 AND workId > ?3))` : '';
  const rows = await db.prepare(`SELECT * FROM (SELECT ${itemColumns('w')},
      lower(coalesce((SELECT t.title FROM work_titles t WHERE t.work_id = w.work_id ORDER BY t.observations DESC, t.title LIMIT 1),
        w.work_id)) AS sortTitle, ${workAggregates}
    FROM works w WHERE ?1 IS NULL OR EXISTS (SELECT 1 FROM work_titles t WHERE t.work_id = w.work_id AND t.normalized LIKE '%' || ?1 || '%'))
    WHERE 1 ${keyset} ORDER BY ${value} ${ascending ? 'ASC' : 'DESC'}, workId LIMIT ${PAGE_SIZE + 1}`)
    .bind(q, ...(cursor ?? [])).all<Record<string, any>>();
  const page = rows.results.slice(0, PAGE_SIZE);
  const details = await pageDetails(db, page.map(row => row.workId));
  const items = page.map(row => {
    const label = itemFromRow(row);
    return { ...label, versions: details.versions.get(row.workId) ?? [], lastChangeAt: ms(row.lastChangeAt), filters: row.filters,
      netVotes: row.netVotes, openReports: row.openReports, uploads: row.uploads, contributors: details.contributors.get(row.workId) ?? [] };
  });
  return { version: 1, observedAt: now * 1000, sort, q: query.q ?? null, items,
    nextCursor: rows.results.length > PAGE_SIZE ? encodeCursor([page[PAGE_SIZE - 1][value], page[PAGE_SIZE - 1].workId]) : null };
}
