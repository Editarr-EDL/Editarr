import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { CatalogModerationError } from './moderation';

const PAGE = 20;
const cursor = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
const versionStatus = z.enum(['confirmed', 'candidate', 'merged_into']);
const suggestionState = z.enum(['pending', 'approved', 'rejected']);

/** Parse a list query with one optional filter and an `after` cursor; unknown or repeated keys fail. */
function listQuery<T extends string>(params: URLSearchParams, filter: string, schema: z.ZodType<T>) {
  if ([...params.keys()].some(key => key !== filter && key !== 'after') ||
      params.getAll(filter).length > 1 || params.getAll('after').length > 1) throw new CatalogModerationError('invalid_moderation');
  const value = params.get(filter), after = params.get('after');
  try {
    return { value: value === null ? null : schema.parse(value), after: after === null ? '' : cursor.parse(after) };
  } catch { throw new CatalogModerationError('invalid_moderation'); }
}

/** `GET /admin/v1/versions?status=&after=`: 20 versions per page in ID order. */
export async function listVersions(db: SqlDatabase, params: URLSearchParams) {
  const query = listQuery(params, 'status', versionStatus);
  const rows = await db.prepare(`SELECT v.version_id AS versionId, v.work_id AS workId, v.status, v.alias_id AS aliasId,
    v.label_text AS labelText, v.merged_into AS mergedInto, v.runtime_ms AS runtimeMs, v.trust, v.revision,
    v.quarantined, v.created_at AS createdAt,
    (SELECT count(DISTINCT c.contributor_id) FROM version_counted_acceptances c WHERE c.version_id = v.version_id) AS contributorCount,
    (SELECT count(*) FROM master_filters f WHERE f.version_id = v.version_id) AS filterCount,
    (SELECT count(*) FROM master_filters f WHERE f.version_id = v.version_id AND f.served = 1) AS servedFilterCount
    FROM versions v WHERE (?1 IS NULL OR v.status = ?1) AND v.version_id > ?2 ORDER BY v.version_id LIMIT ${PAGE + 1}`)
    .bind(query.value, query.after).all<{ versionId: string }>();
  return { version: 1, versions: rows.results.slice(0, PAGE),
    nextCursor: rows.results.length > PAGE ? rows.results[PAGE - 1].versionId : null };
}

/** `GET /admin/v1/alias-suggestions?state=&after=`: 20 suggestions per page in ID order. */
export async function listAliasSuggestions(db: SqlDatabase, params: URLSearchParams) {
  const query = listQuery(params, 'state', suggestionState);
  const rows = await db.prepare(`SELECT suggestion_id AS suggestionId, normalized, text, submission_id AS submissionId,
    state, alias_id AS aliasId, created_at AS createdAt FROM alias_suggestions
    WHERE (?1 IS NULL OR state = ?1) AND suggestion_id > ?2 ORDER BY suggestion_id LIMIT ${PAGE + 1}`)
    .bind(query.value, query.after).all<{ suggestionId: string }>();
  return { version: 1, suggestions: rows.results.slice(0, PAGE),
    nextCursor: rows.results.length > PAGE ? rows.results[PAGE - 1].suggestionId : null };
}

/** Inspect one version or alias suggestion with its optimistic-write revision and bounded audit. */
export async function inspectCatalogResource(db: SqlDatabase, kind: 'version' | 'alias_suggestion', targetId: string) {
  const resource = `${kind}:${targetId}`;
  const state = kind === 'version'
    ? db.prepare(`SELECT version_id AS versionId, work_id AS workId, status, alias_id AS aliasId, label_text AS labelText,
      merged_into AS mergedInto, runtime_ms AS runtimeMs, trust, revision, reference_submission_id AS referenceSubmissionId,
      quarantined, created_at AS createdAt FROM versions WHERE version_id = ?`).bind(targetId)
    : db.prepare(`SELECT suggestion_id AS suggestionId, normalized, text, submission_id AS submissionId, state,
      alias_id AS aliasId, created_at AS createdAt FROM alias_suggestions WHERE suggestion_id = ?`).bind(targetId);
  const [row, revision, audit] = await db.batch<Record<string, unknown>>([state,
    db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(resource),
    db.prepare(`SELECT action_id AS actionId, actor, command, reason, outcome, error_code AS errorCode,
      expected_revision AS expectedRevision, created_at AS createdAt FROM catalog_moderation_actions
      WHERE resource = ? ORDER BY created_at DESC, action_id DESC LIMIT 20`).bind(resource)]);
  if (!row.results.length) throw new CatalogModerationError('resource_not_found');
  return { version: 1, resource, revision: Number(revision.results[0]?.revision ?? 0), state: row.results[0], audit: audit.results };
}
