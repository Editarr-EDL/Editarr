import type { SqlDatabase } from '../../runtime/contracts';
import { z } from 'zod';
import { resolveLabels, type ContributorLabel, type FilterLabel, type ItemLabel, type VersionLabel } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, InsightQueryError, PAGE_SIZE, parseQuery } from './query';
import { filterText, itemText } from './resources';

export const QUEUE_KINDS = ['all', 'reports', 'aliases', 'versions', 'quarantined', 'disputed'] as const;
export type QueueKind = typeof QUEUE_KINDS[number];
type Source = Exclude<QueueKind, 'all'>;

/** One open decision. `resource`/`revision` are the moderation target and its optimistic-write token. */
export interface QueueItem {
  kind: 'report' | 'alias' | 'version' | 'quarantined' | 'disputed'; id: string; at: number;
  resource: string; revision: number; target: { type: string; id: string };
  title: { targetType: string; text: string };
  item: ItemLabel | null; version: VersionLabel | null; filter: FilterLabel | null;
  reporter: ContributorLabel | null; reason: string | null; quote: string | null;
  suggestion: { text: string; normalized: string } | null;
  impact: Record<string, number | null> | null;
  latestAction: { actor: string; command: string; reason: string; at: number } | null;
}

const revision = (resource: string) => `coalesce((SELECT mv.revision FROM moderation_versions mv WHERE mv.resource = ${resource}), 0)`;
const latest = (resource: string, column: string) => `(SELECT ${column} FROM moderation_actions ma WHERE ma.resource = ${resource}
  AND ma.outcome = 'applied' AND ma.command = 'quarantine' ORDER BY ma.created_at DESC, ma.action_id DESC LIMIT 1)`;
const filterImpact = (id: string) => `(SELECT json_object('served', mf.served, 'confidence', mf.confidence, 'upvotes', mf.upvotes,
  'downvotes', mf.downvotes, 'disputes', mf.disputes, 'attestations', mf.attestations) FROM master_filters mf WHERE mf.filter_id = ${id})`;
/** Version impact; `id` must be a qualified outer column so the inner aliases cannot capture it. */
const versionImpact = (id: string) => `json_object('runtimeMs', (SELECT vi.runtime_ms FROM versions vi WHERE vi.version_id = ${id}),
  'trust', (SELECT vi.trust FROM versions vi WHERE vi.version_id = ${id}),
  'uploads', (SELECT count(*) FROM version_acceptances va WHERE va.version_id = ${id}),
  'contributors', (SELECT count(DISTINCT ca.contributor_id) FROM version_counted_acceptances ca WHERE ca.version_id = ${id}),
  'filters', (SELECT count(*) FROM master_filters mf WHERE mf.version_id = ${id}))`;
/** Keyset bound shared by every source: `?1` is the cursor time (ms), `?2` the cursor ID, `?3` the limit. */
const before = (at: string, id: string) => `(${at} < ?1 OR (${at} = ?1 AND ${id} < ?2))`;

/** Quarantined filters, versions and submissions as `q(key, targetType, targetId, createdAt)`, read through partial indexes. */
const quarantinedSet = `(SELECT 'filter:' || filter_id AS key, 'filter' AS targetType, filter_id AS targetId, NULL AS createdAt
      FROM filter_registry WHERE quarantined = 1
    UNION ALL SELECT 'version:' || version_id, 'version', version_id, created_at * 1000 FROM versions WHERE quarantined = 1
    UNION ALL SELECT 'submission:' || submission_id, 'submission', submission_id, accepted_at * 1000
      FROM version_acceptances WHERE quarantined = 1) q`;
/** A quarantined resource waits since its latest applied quarantine action (else its creation). */
const quarantinedAt = `coalesce(${latest('q.key', 'ma.created_at')}, q.createdAt, 0)`;
/** Served-or-not master filter `f` that is disputed (disputes, or more down than up votes) and not quarantined. */
const disputedWhere = `(f.disputes > 0 OR f.downvotes > f.upvotes)
  AND NOT EXISTS (SELECT 1 FROM filter_registry r WHERE r.filter_id = f.filter_id AND r.quarantined = 1)`;
/** A disputed filter waits since its latest vote event, else its version's latest revision. */
const disputedAt = `coalesce((SELECT max(ve.created_at) * 1000 FROM vote_events ve WHERE ve.filter_id = f.filter_id),
  (SELECT max(mr.generated_at) * 1000 FROM master_revisions mr WHERE mr.version_id = f.version_id), 0)`;

// Quarantined resources take their time and reason from the latest applied quarantine action.
const quarantinedSql = `SELECT * FROM (SELECT 'quarantined' AS kind, 'quarantined:' || q.key AS key, q.key AS id,
    ${quarantinedAt} AS at, q.key AS resource, ${revision('q.key')} AS revision,
    q.targetType, q.targetId, ${latest('q.key', "json_object('actor', ma.actor, 'command', ma.command, 'reason', ma.reason, 'at', ma.created_at)")} AS action,
    CASE q.targetType WHEN 'submission' THEN (SELECT va.contributor_id FROM version_acceptances va WHERE va.submission_id = q.targetId) END AS reporter,
    CASE q.targetType WHEN 'filter' THEN ${filterImpact('q.targetId')} WHEN 'version' THEN ${versionImpact('q.targetId')}
      ELSE json_object('entries', (SELECT count(*) FROM submission_entries se WHERE se.submission_id = q.targetId)) END AS impact
  FROM ${quarantinedSet})
  WHERE ${before('at', 'key')} ORDER BY at DESC, key DESC LIMIT ?3`;

/** Per-source SQL. Each reads an open set through its queue or partial index, then sorts newest first. */
const sources: Record<Source, string> = {
  reports: `SELECT * FROM (SELECT 'report' AS kind, 'report:' || rp.report_id AS key, rp.report_id AS id, rp.created_at * 1000 AS at,
      'report:' || rp.report_id AS resource, ${revision("'report:' || rp.report_id")} AS revision, rp.target_type AS targetType,
      rp.target_id AS targetId, rp.contributor_id AS reporter, rp.reason, rp.note AS quote,
      CASE rp.target_type WHEN 'filter' THEN ${filterImpact('rp.target_id')} ELSE ${versionImpact('rp.target_id')} END AS impact
    FROM reports rp WHERE rp.state = 'open') WHERE ${before('at', 'key')} ORDER BY at DESC, key DESC LIMIT ?3`,
  aliases: `SELECT * FROM (SELECT 'alias' AS kind, 'alias:' || s.suggestion_id AS key, s.suggestion_id AS id, s.created_at * 1000 AS at,
      'alias_suggestion:' || s.suggestion_id AS resource, ${revision("'alias_suggestion:' || s.suggestion_id")} AS revision,
      'alias_suggestion' AS targetType, s.suggestion_id AS targetId, s.submission_id AS submissionId, a.contributor_id AS reporter,
      s.text, s.normalized, json_object('versionsWithLabel', (SELECT count(*) FROM versions v WHERE v.work_id =
        (SELECT work_id FROM versions WHERE version_id = a.version_id) AND v.label_text = s.normalized AND v.status != 'merged_into')) AS impact
    FROM alias_suggestions s LEFT JOIN version_acceptances a ON a.submission_id = s.submission_id WHERE s.state = 'pending')
    WHERE ${before('at', 'key')} ORDER BY at DESC, key DESC LIMIT ?3`,
  versions: `SELECT * FROM (SELECT 'version' AS kind, 'version:' || v.version_id AS key, v.version_id AS id, v.created_at * 1000 AS at,
      'version:' || v.version_id AS resource, ${revision("'version:' || v.version_id")} AS revision, 'version' AS targetType,
      v.version_id AS targetId, ${versionImpact('v.version_id')} AS impact FROM versions v WHERE v.status = 'candidate')
    WHERE ${before('at', 'key')} ORDER BY at DESC, key DESC LIMIT ?3`,
  quarantined: quarantinedSql,
  disputed: `SELECT * FROM (SELECT 'disputed' AS kind, 'disputed:' || f.filter_id AS key, f.filter_id AS id,
      ${disputedAt} AS at,
      'filter:' || f.filter_id AS resource, ${revision("'filter:' || f.filter_id")} AS revision, 'filter' AS targetType,
      f.filter_id AS targetId, ${filterImpact('f.filter_id')} AS impact
    FROM master_filters f WHERE ${disputedWhere})
    WHERE ${before('at', 'key')} ORDER BY at DESC, key DESC LIMIT ?3`,
};
/**
 * Open-decision counts and oldest waiting time (epoch ms; null when empty) per queue kind, in one
 * statement. Each kind reads its open set through the same queue or partial index as its list, and
 * its time matches the list item's `at`.
 */
export function queueCountsStatement(db: SqlDatabase) {
  return db.prepare(`SELECT (SELECT count(*) FROM reports WHERE state = 'open') AS reports,
    (SELECT min(created_at) * 1000 FROM reports WHERE state = 'open') AS reportsOldest,
    (SELECT count(*) FROM alias_suggestions WHERE state = 'pending') AS aliases,
    (SELECT min(created_at) * 1000 FROM alias_suggestions WHERE state = 'pending') AS aliasesOldest,
    (SELECT count(*) FROM versions WHERE status = 'candidate') AS versions,
    (SELECT min(created_at) * 1000 FROM versions WHERE status = 'candidate') AS versionsOldest,
    (SELECT count(*) FROM ${quarantinedSet}) AS quarantined,
    (SELECT min(${quarantinedAt}) FROM ${quarantinedSet}) AS quarantinedOldest,
    (SELECT count(*) FROM master_filters f WHERE ${disputedWhere}) AS disputed,
    (SELECT min(${disputedAt}) FROM master_filters f WHERE ${disputedWhere}) AS disputedOldest`);
}
const SOURCES: Source[] = ['reports', 'aliases', 'versions', 'quarantined', 'disputed'];
/** Open decisions per kind; `oldestAt` is the whole queue's oldest waiting time (epoch ms, null when empty). */
export type QueueCounts = Record<Source | 'all', number> & { oldestAt: number | null };
/** Oldest waiting time per kind and for the whole queue (epoch ms, null when that kind is empty). */
export type QueueOldest = Record<Source | 'all', number | null>;
export function queueCounts(row: Record<string, number | null>): QueueCounts {
  const counts = Object.fromEntries(SOURCES.map(source => [source, Number(row[source])])) as Record<Source, number>;
  return { all: SOURCES.reduce((sum, source) => sum + counts[source], 0), ...counts, oldestAt: queueOldest(row).all };
}
export function queueOldest(row: Record<string, number | null>): QueueOldest {
  const oldest = Object.fromEntries(SOURCES.map(source => [source, row[`${source}Oldest`] ?? null])) as Record<Source, number | null>;
  const present = Object.values(oldest).filter((value): value is number => value !== null);
  return { all: present.length ? Math.min(...present) : null, ...oldest };
}

type Raw = Record<string, any>;
const parse = (value: string | null) => value ? JSON.parse(value) as Record<string, any> : null;

/** Read up to `limit` queue items of `kind` older than the `(at, key)` cursor, newest first, labelled. */
export async function readQueue(db: SqlDatabase, kind: QueueKind, cursor: [number, string] | null, limit: number) {
  const kinds: Source[] = kind === 'all' ? ['reports', 'aliases', 'versions', 'quarantined', 'disputed'] : [kind];
  const [at, key] = cursor ?? [Number.MAX_SAFE_INTEGER, '\uffff'];
  const results = await db.batch<Raw>(kinds.map(source => db.prepare(sources[source]).bind(at, key, limit + 1)));
  const rows = results.flatMap(result => result.results)
    .sort((a, b) => b.at - a.at || (a.key < b.key ? 1 : a.key > b.key ? -1 : 0)).slice(0, limit + 1);
  const page = rows.slice(0, limit);
  const of = (type: string) => page.filter(row => row.targetType === type).map(row => row.targetId as string);
  const labels = await resolveLabels(db, { filters: of('filter'), versions: of('version'),
    submissions: [...of('submission'), ...page.map(row => row.submissionId)], contributors: page.map(row => row.reporter) });
  const items = page.map((row): QueueItem => {
    const type = row.targetType === 'alias_suggestion' ? 'submission' : row.targetType;
    const target = labels.target(type, type === 'submission' ? row.submissionId ?? row.targetId : row.targetId);
    const action = parse(row.action);
    const text = [target.filter ? filterText(target.filter) : '', target.item ? itemText(target.item) : '', target.version?.label ?? '']
      .filter(Boolean).join(' · ') || row.targetId;
    return { kind: row.kind, id: row.id, at: Number(row.at), resource: row.resource, revision: Number(row.revision),
      target: { type: row.targetType, id: row.targetId }, title: { targetType: row.targetType, text }, ...target,
      reporter: labels.contributor(row.reporter), reason: row.reason ?? action?.reason ?? null, quote: row.quote ?? null,
      suggestion: row.kind === 'alias' ? { text: row.text, normalized: row.normalized } : null, impact: parse(row.impact),
      latestAction: action ? { actor: action.actor, command: action.command, reason: action.reason, at: Number(action.at) } : null };
  });
  return { items, nextCursor: rows.length > limit ? encodeCursor([page[limit - 1].at, page[limit - 1].key]) : null };
}

/** `GET /admin/v1/queue?kind=&after=`. */
export async function queuePage(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_queue_query';
  const query = parseQuery(url, code, { kind: z.enum(QUEUE_KINDS).optional(), after: cursorParam.optional() });
  const cursor = decodeCursor(query.after, ['n', 's'], code) as [number, string] | null;
  if (cursor && !/^(report|alias|version|quarantined|disputed):/.test(cursor[1])) throw new InsightQueryError(code);
  const [counts, page] = await Promise.all([queueCountsStatement(db).first<Record<string, number | null>>(),
    readQueue(db, query.kind ?? 'all', cursor, PAGE_SIZE)]);
  const kind = query.kind ?? 'all', oldest = queueOldest(counts!);
  return { version: 1, observedAt: now * 1000, kind, counts: queueCounts(counts!), oldest, oldestAt: oldest[kind], ...page };
}
