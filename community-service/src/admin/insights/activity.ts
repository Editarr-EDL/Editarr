import type { SqlDatabase } from '../../runtime/contracts';
import { resolveLabels, type ContributorLabel, type FilterLabel, type ItemLabel, type VersionLabel } from '../labels';
import { cursorParam, decodeCursor, encodeCursor, InsightQueryError, PAGE_SIZE, parseQuery } from './query';
import { z } from 'zod';
import { loadIndirect, parseResource, resourceLabel, resourceLabelRequest } from './resources';

export const ACTIVITY_KINDS = ['all', 'uploads', 'votes', 'reports', 'moderation'] as const;
export type ActivityKind = typeof ACTIVITY_KINDS[number];

export type Actor = { type: 'contributor'; contributorId: string; displayName: string } | { type: 'operator'; subject: string };
/** One feed event; the UI renders the sentence from `verb` and the labelled parts. */
export interface ActivityEvent {
  id: string; kind: Exclude<ActivityKind, 'all'>; at: number; actor: Actor | null; verb: string;
  item: ItemLabel | null; version: VersionLabel | null; filter: FilterLabel | null;
  subject: ContributorLabel | null; detail: Record<string, string | number | null>;
}

type Raw = { id: string; at: number; kind: ActivityEvent['kind']; [key: string]: any };

/**
 * Per-source SQL, newest first. `?1` is the cursor time in ms, `?2` the cursor ID and `?3` the
 * limit. Seconds-based sources bound `created_at` by `?1 / 1000` first so the time index is used.
 */
const sources: Record<ActivityEvent['kind'], string> = {
  uploads: `SELECT 'u:' || s.submission_id AS id, s.created_at * 1000 AS at, 'uploads' AS kind, s.contributor_id AS contributorId,
      s.submission_id AS submissionId, s.version_id AS versionId, s.state,
      (SELECT count(*) FROM submission_entries e WHERE e.submission_id = s.submission_id) AS entries
    FROM submissions s WHERE s.created_at <= ?1 / 1000 AND (s.created_at * 1000 < ?1 OR 'u:' || s.submission_id < ?2)
    ORDER BY s.created_at DESC, s.submission_id DESC LIMIT ?3`,
  votes: `SELECT 'v:' || printf('%020d', e.event_id) AS id, e.created_at * 1000 AS at, 'votes' AS kind, e.contributor_id AS contributorId,
      e.filter_id AS filterId, e.vote, e.kind AS eventKind
    FROM vote_events e WHERE e.created_at <= ?1 / 1000 AND (e.created_at * 1000 < ?1 OR 'v:' || printf('%020d', e.event_id) < ?2)
    ORDER BY e.created_at DESC, e.event_id DESC LIMIT ?3`,
  reports: `SELECT 'r:' || r.report_id AS id, r.created_at * 1000 AS at, 'reports' AS kind, r.contributor_id AS contributorId,
      r.report_id AS reportId, r.target_type AS targetType, r.target_id AS targetId, r.reason, r.state
    FROM reports r WHERE r.created_at <= ?1 / 1000 AND (r.created_at * 1000 < ?1 OR 'r:' || r.report_id < ?2)
    ORDER BY r.created_at DESC, r.report_id DESC LIMIT ?3`,
  moderation: `SELECT * FROM (SELECT 'm:' || action_id AS id, created_at AS at, 'moderation' AS kind, actor, resource, command, reason
      FROM moderation_actions WHERE outcome = 'applied' AND created_at <= ?1 AND (created_at < ?1 OR 'm:' || action_id < ?2)
      ORDER BY created_at DESC, action_id DESC LIMIT ?3)
    UNION ALL SELECT * FROM (SELECT 'm:' || action_id, created_at, 'moderation', actor, resource, command, reason
      FROM catalog_moderation_actions WHERE outcome = 'applied' AND created_at <= ?1 AND (created_at < ?1 OR 'm:' || action_id < ?2)
      ORDER BY created_at DESC, action_id DESC LIMIT ?3)`,
};

const voteVerbs: Record<string, (vote: string) => string> = {
  cast: vote => `voted_${vote}`, changed: vote => `changed_vote_${vote}`, removed: () => 'removed_vote',
  excluded: () => 'vote_excluded', restored: () => 'vote_restored',
};

/**
 * Read up to `limit` events of `kind` strictly before the `(at, id)` cursor, newest first, with
 * labels. Each source contributes at most `limit + 1` rows, so a page costs one statement per source
 * plus the label lookups.
 */
export async function readActivity(db: SqlDatabase, kind: ActivityKind, before: [number, string] | null, limit: number) {
  const kinds = kind === 'all' ? (['uploads', 'votes', 'reports', 'moderation'] as const) : [kind];
  const cursor = before ?? [Number.MAX_SAFE_INTEGER, '\uffff'];
  const results = await db.batch<Raw>(kinds.map(source => db.prepare(sources[source]).bind(cursor[0], cursor[1], limit + 1)));
  const rows = results.flatMap(result => result.results)
    .sort((a, b) => b.at - a.at || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0)).slice(0, limit + 1);
  const page = rows.slice(0, limit);
  const refs = page.filter(row => row.kind === 'moderation').map(row => parseResource(row.resource));
  const indirect = await loadIndirect(db, refs);
  const fromResources = resourceLabelRequest(refs, indirect);
  const labels = await resolveLabels(db, {
    filters: [...fromResources.filters, ...page.map(row => row.filterId ?? (row.targetType === 'filter' ? row.targetId : null))],
    versions: [...fromResources.versions, ...page.map(row => row.versionId ?? (row.targetType === 'version' ? row.targetId : null))],
    submissions: fromResources.submissions, works: fromResources.works,
    contributors: [...fromResources.contributors, ...page.map(row => row.contributorId)],
  });
  const contributor = (id: string): Actor => ({ type: 'contributor', ...labels.contributor(id)! });
  const items = page.map((row): ActivityEvent => {
    const base = { id: row.id, kind: row.kind, at: Number(row.at), subject: null };
    if (row.kind === 'uploads') return { ...base, actor: contributor(row.contributorId), verb: 'uploaded',
      ...labels.target('version', row.versionId), detail: { submissionId: row.submissionId, entries: row.entries, state: row.state } };
    if (row.kind === 'votes') return { ...base, actor: contributor(row.contributorId), verb: voteVerbs[row.eventKind]?.(row.vote) ?? 'voted',
      ...labels.target('filter', row.filterId), detail: { vote: row.vote } };
    if (row.kind === 'reports') return { ...base, actor: contributor(row.contributorId), verb: 'reported',
      ...labels.target(row.targetType, row.targetId), detail: { reportId: row.reportId, reason: row.reason, state: row.state, targetType: row.targetType } };
    const label = resourceLabel(parseResource(row.resource), labels, indirect);
    return { ...base, actor: { type: 'operator', subject: row.actor }, verb: row.command, item: label.item, version: label.version,
      filter: label.filter, subject: label.contributor,
      detail: { resource: row.resource, resourceType: label.type, resourceText: label.text, reason: row.reason } };
  });
  return { items, nextCursor: rows.length > limit ? encodeCursor([page[limit - 1].at, page[limit - 1].id]) : null };
}

/** `GET /admin/v1/activity?kind=&before=`. */
export async function activityPage(db: SqlDatabase, url: URL, now: number) {
  const code = 'invalid_activity_query';
  const query = parseQuery(url, code, { kind: z.enum(ACTIVITY_KINDS).optional(), before: cursorParam.optional() });
  const before = decodeCursor(query.before, ['n', 's'], code) as [number, string] | null;
  if (before && !/^[urvm]:/.test(before[1])) throw new InsightQueryError(code);
  return { version: 1, observedAt: now * 1000, kind: query.kind ?? 'all', ...await readActivity(db, query.kind ?? 'all', before, PAGE_SIZE) };
}
