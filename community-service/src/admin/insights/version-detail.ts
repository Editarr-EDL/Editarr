import type { SqlDatabase } from '../../runtime/contracts';
import { filterFromRow, itemColumns, itemFromRow, versionLabelSql } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, InsightNotFound, parseQuery } from './query';
import { summariseRevisions } from './revisions';

const MAX_FILTERS = 2000;
const REVISIONS_PAGE = 10;

/** Registry filter columns with stats, open reports and the moderation token; `r` is `filter_registry`. */
export const filterDetailColumns = `r.filter_id AS filterId, f.category, coalesce(f.subcategory, r.incumbent_subcategory) AS subcategory,
  coalesce(f.action, r.incumbent_action) AS action, f.start_ms AS startMs, f.end_ms AS endMs,
  coalesce(f.expression, r.incumbent_expression) AS expression, r.version_id AS versionId, r.state, r.merged_into AS mergedInto,
  r.quarantined, f.severity, f.confidence, f.spread_start_ms AS spreadStartMs, f.spread_end_ms AS spreadEndMs,
  f.contributor_count AS contributorCount, f.attestations, f.upvotes, f.downvotes, f.disputes, f.coverage_negatives AS coverageNegatives,
  f.low_confidence AS lowConfidence, f.served,
  (SELECT count(*) FROM reports p WHERE p.target_type = 'filter' AND p.target_id = r.filter_id AND p.state = 'open') AS openReports,
  coalesce((SELECT revision FROM moderation_versions WHERE resource = 'filter:' || r.filter_id), 0) AS moderationRevision`;

/** Shape a `filterDetailColumns` row: label fields plus stats; registry-only filters keep null stats. */
export function filterDetail(row: Record<string, any>) {
  return { ...filterFromRow(row), state: row.state, mergedInto: row.mergedInto, quarantined: row.quarantined === 1,
    severity: row.severity, confidence: row.confidence,
    spread: row.spreadStartMs === null ? null : { startMs: row.spreadStartMs, endMs: row.spreadEndMs },
    contributorCount: row.contributorCount, attestations: row.attestations, upvotes: row.upvotes, downvotes: row.downvotes,
    disputes: row.disputes, coverageNegatives: row.coverageNegatives,
    lowConfidence: row.lowConfidence === null ? null : row.lowConfidence === 1, served: row.served === null ? null : row.served === 1,
    openReports: row.openReports, moderation: { resource: `filter:${row.filterId}`, revision: row.moderationRevision } };
}

/**
 * `GET /admin/v1/items/{workId}/versions/{versionId}?before=`: the version's registry filters (up to
 * 2000, `truncated` beyond) and a page of 10 revisions, newest first, before the cursor revision.
 */
export async function versionDetail(db: SqlDatabase, url: URL, workId: string, versionId: string, now: number) {
  const code = 'invalid_item_query';
  const query = parseQuery(url, code, { before: cursorParam.optional() });
  const before = Number(decodeCursor(query.before, ['n'], code)?.[0] ?? Number.MAX_SAFE_INTEGER);
  const [version, filters, revisions] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT v.version_id AS versionId, ${versionLabelSql('v')} AS label, v.status, v.merged_into AS mergedInto, v.quarantined,
      v.runtime_ms AS runtimeMs, v.trust, v.revision, ${itemColumns('w')} FROM versions v JOIN works w ON w.work_id = v.work_id
      WHERE v.version_id = ? AND v.work_id = ?`).bind(versionId, workId),
    db.prepare(`SELECT ${filterDetailColumns} FROM filter_registry r LEFT JOIN master_filters f ON f.filter_id = r.filter_id
      WHERE r.version_id = ? ORDER BY coalesce(f.start_ms, 0), r.filter_id LIMIT ${MAX_FILTERS + 1}`).bind(versionId),
    db.prepare(`SELECT version_id AS versionId, revision, generated_at AS generatedAt FROM master_revisions
      WHERE version_id = ? AND revision < ? ORDER BY revision DESC LIMIT ${REVISIONS_PAGE + 1}`).bind(versionId, before)]);
  if (!version.results.length) throw new InsightNotFound();
  const row = version.results[0];
  const page = revisions.results.slice(0, REVISIONS_PAGE) as { versionId: string; revision: number; generatedAt: number }[];
  const summaries = await summariseRevisions(db, page);
  return {
    version: 1, observedAt: now * 1000, item: itemFromRow(row),
    versionInfo: { versionId: row.versionId, label: row.label, status: row.status, mergedInto: row.mergedInto,
      quarantined: row.quarantined === 1, runtimeMs: row.runtimeMs, trust: row.trust, revision: row.revision },
    filters: filters.results.slice(0, MAX_FILTERS).map(filterDetail), truncated: filters.results.length > MAX_FILTERS,
    revisions: summaries.map(({ versionId: _, ...summary }) => ({ ...summary, current: summary.revision === row.revision })),
    revisionsNextCursor: revisions.results.length > REVISIONS_PAGE ? encodeCursor([page[REVISIONS_PAGE - 1].revision]) : null,
  };
}
