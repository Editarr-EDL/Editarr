import type { SqlDatabase } from '../../runtime/contracts';
import { z } from 'zod';
import { normalize } from '../../versions/normalize';
import { resolveLabels } from '../labels';
import { parseQuery } from './query';
import { filterText, itemText } from './resources';

const ID_KINDS = ['work', 'version', 'filter', 'submission', 'contributor', 'report', 'alias_suggestion'] as const;

/**
 * `GET /admin/v1/search?q=`: up to 20 results in total — exact IDs first, then titles matching the
 * normalized query (substring) and display names containing it.
 */
export async function search(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_search_query';
  const query = parseQuery(url, code, { q: z.string().trim().min(1).max(100) });
  const q = query.q, title = normalize(q), escaped = q.replace(/[\\%_]/g, match => `\\${match}`);
  const isId = /^[A-Za-z0-9_.-]{1,128}$/.test(q);
  const [ids, items, contributors] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT kind, id FROM (
        SELECT 'work' AS kind, work_id AS id FROM works WHERE work_id = ?1
        UNION ALL SELECT 'version', version_id FROM versions WHERE version_id = ?1
        UNION ALL SELECT 'filter', filter_id FROM filter_registry WHERE filter_id = ?1
        UNION ALL SELECT 'submission', submission_id FROM version_acceptances WHERE submission_id = ?1
        UNION ALL SELECT 'contributor', contributor_id FROM contributors WHERE contributor_id = ?1
        UNION ALL SELECT 'report', report_id FROM reports WHERE report_id = ?1
        UNION ALL SELECT 'alias_suggestion', suggestion_id FROM alias_suggestions WHERE suggestion_id = ?1)
      WHERE ?2 LIMIT 7`).bind(q, Number(isId)),
    db.prepare(`SELECT work_id AS workId FROM work_titles WHERE ?1 != '' AND normalized LIKE '%' || ?1 || '%' GROUP BY work_id
      ORDER BY max(normalized = ?1) DESC, max(normalized LIKE ?1 || '%') DESC, work_id LIMIT 10`).bind(title),
    db.prepare(`SELECT contributor_id AS contributorId, display_name AS displayName FROM contributors
      WHERE display_name LIKE '%' || ?1 || '%' ESCAPE '\\' ORDER BY (display_name = ?2) DESC, display_name_key, contributor_id LIMIT 10`)
      .bind(escaped, q)]);
  const id = (kind: string) => ids.results.filter(row => row.kind === kind).map(row => row.id as string);
  const reportTargets = id('report').length ? (await db.prepare(`SELECT report_id AS id, target_type AS type, target_id AS target
    FROM reports WHERE report_id = ?`).bind(q).all<Record<string, any>>()).results : [];
  const reportOn = (type: string) => reportTargets.filter(row => row.type === type).map(row => row.target as string);
  const labels = await resolveLabels(db, { works: [...id('work'), ...items.results.map(row => row.workId)],
    versions: [...id('version'), ...reportOn('version')], filters: [...id('filter'), ...reportOn('filter')],
    submissions: id('submission'), contributors: id('contributor') });
  const describe = (kind: typeof ID_KINDS[number], value: string): string => {
    if (kind === 'work') return itemText(labels.item(value)!);
    if (kind === 'contributor') return labels.contributor(value)!.displayName;
    if (kind === 'filter') {
      const target = labels.target('filter', value);
      return [filterText(target.filter!), target.item && itemText(target.item), target.version?.label].filter(Boolean).join(' · ');
    }
    if (kind === 'version' || kind === 'submission') {
      const target = labels.target(kind, value);
      return [kind === 'submission' ? 'Upload' : '', target.item && itemText(target.item), target.version?.label]
        .filter(Boolean).join(' · ') || value;
    }
    if (kind === 'report') {
      const report = reportTargets[0];
      if (!report) return `Report ${value}`;
      const target = labels.target(report.type, report.target);
      return ['Report', target.filter && filterText(target.filter), target.item && itemText(target.item), target.version?.label]
        .filter(Boolean).join(' · ');
    }
    return `Alias suggestion ${value}`;
  };
  const idResults = ids.results.map(row => ({ kind: row.kind, id: row.id, label: describe(row.kind, row.id) }));
  const itemResults = items.results.slice(0, Math.max(0, 20 - idResults.length)).map(row => labels.item(row.workId)!);
  const contributorResults = contributors.results.slice(0, Math.max(0, 20 - idResults.length - itemResults.length))
    .map(row => ({ contributorId: row.contributorId, displayName: row.displayName }));
  return { version: 1, observedAt: now * 1000, q, items: itemResults, contributors: contributorResults, ids: idResults };
}
