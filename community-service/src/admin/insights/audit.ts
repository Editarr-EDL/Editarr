import type { SqlDatabase } from '../../runtime/contracts';
import { resolveLabels } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, PAGE_SIZE, parseQuery } from './query';
import { loadIndirect, parseResource, resourceLabel, resourceLabelRequest } from './resources';

const columns = `action_id AS actionId, actor, resource, target_id AS targetId, command, reason, outcome, error_code AS errorCode,
  expected_revision AS expectedRevision, created_at AS createdAt`;
const newest = (table: string) => `SELECT ${columns} FROM ${table} WHERE created_at <= ?1 AND (created_at < ?1 OR action_id < ?2)
  ORDER BY created_at DESC, action_id DESC LIMIT ?3`;

/**
 * `GET /admin/v1/audit?after=`: moderation and catalog audit rows (every outcome), newest first,
 * 20 per page, each with a readable `resourceLabel` resolved from item, version, filter and contributor labels.
 */
export async function auditPage(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_audit_query';
  const query = parseQuery(url, code, { after: cursorParam.optional() });
  const [at, id] = decodeCursor(query.after, ['n', 's'], code) ?? [Number.MAX_SAFE_INTEGER, '\uffff'];
  const results = await db.batch<Record<string, any>>([db.prepare(newest('moderation_actions')).bind(at, id, PAGE_SIZE + 1),
    db.prepare(newest('catalog_moderation_actions')).bind(at, id, PAGE_SIZE + 1)]);
  const rows = results.flatMap(result => result.results)
    .sort((a, b) => b.createdAt - a.createdAt || (a.actionId < b.actionId ? 1 : a.actionId > b.actionId ? -1 : 0));
  const page = rows.slice(0, PAGE_SIZE);
  const refs = page.map(row => parseResource(row.resource));
  const indirect = await loadIndirect(db, refs);
  const labels = await resolveLabels(db, resourceLabelRequest(refs, indirect));
  return { version: 1, observedAt: now * 1000, source: 'service_database',
    items: page.map((row, index) => ({ ...row, resourceLabel: resourceLabel(refs[index], labels, indirect) })),
    nextCursor: rows.length > PAGE_SIZE ? encodeCursor([page[PAGE_SIZE - 1].createdAt, page[PAGE_SIZE - 1].actionId]) : null };
}
