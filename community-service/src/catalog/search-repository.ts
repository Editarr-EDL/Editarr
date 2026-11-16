import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { CatalogRow } from './lookup';
import { SQL_BIND_LIMIT } from './records';
import { SearchError, type SearchLimits, type SearchTarget } from './search-input';

export interface SearchRow extends CatalogRow { contribution_id: string; target_id: string; candidate_count: number; created_at: number }
export interface PublicState {
  revision_id: string; display_name: string; imports: number; likes: number; dislikes: number;
}
type Key = (string | number)[];
type Strategy = { columns: string; join: string; keys: Key[]; width: number };

export function hasLookupKeys(target: SearchTarget) {
  const evidence = target.evidence;
  return evidence.media.identifiers.length > 0 ||
    (evidence.media.series_identifiers.length > 0 &&
      evidence.media.episode_numbers.length + evidence.media.episode_span.length > 0) ||
    evidence.fingerprints.length > 0 ||
    [evidence.audio_stream, evidence.video_stream].some(item => item?.algorithm && item.digest);
}

function strategies(targets: SearchTarget[]): Strategy[] {
  const identifiers: Key[] = [], episodes: Key[] = [], fingerprints: Key[] = [];
  for (const target of targets) {
    const evidence = target.evidence;
    for (const item of evidence.media.identifiers)
      identifiers.push([target.id, item.provider, item.entity_type, item.value]);
    for (const series of evidence.media.series_identifiers)
      for (const number of [...evidence.media.episode_numbers, ...evidence.media.episode_span])
        episodes.push([target.id, series.provider, series.value, number.scheme, number.season ?? -1, number.number]);
    for (const item of evidence.fingerprints)
      fingerprints.push([target.id, 'file', item.algorithm, item.digest]);
    for (const channel of ['audio', 'video'] as const) {
      const item = evidence[`${channel}_stream`];
      if (item?.algorithm && item.digest) fingerprints.push([target.id, channel, item.algorithm, item.digest]);
    }
  }
  const distinct = (keys: Key[]) => [...new Map(keys.map(key => [JSON.stringify(key), key])).values()];
  return [
    { columns: 'target_id, provider, entity_type, value', width: 4, keys: distinct(identifiers),
      join: `CROSS JOIN work_identifiers w ON w.provider = q.provider AND w.entity_type = q.entity_type AND w.value = q.value
        CROSS JOIN release_work_associations a ON a.work_id = w.work_id
        CROSS JOIN contribution_revisions r ON r.release_id = a.release_id` },
    { columns: 'target_id, provider, value, scheme, season, number', width: 6, keys: distinct(episodes),
      join: `CROSS JOIN work_identifiers w ON w.provider = q.provider AND w.entity_type = 'series' AND w.value = q.value
        CROSS JOIN release_episodes e ON e.series_work_id = w.work_id AND e.scheme = q.scheme AND e.season = q.season AND e.episode_number = q.number
        CROSS JOIN contribution_revisions r ON r.release_id = e.release_id` },
    { columns: 'target_id, channel, algorithm, digest', width: 4, keys: distinct(fingerprints),
      join: `CROSS JOIN release_fingerprints f ON f.channel = q.channel AND f.algorithm = q.algorithm AND f.digest = q.digest
        CROSS JOIN contribution_revisions r ON r.release_id = f.release_id` },
  ];
}

export function searchPlans(targets: SearchTarget[], ceiling: number, limits: SearchLimits) {
  const groups = strategies(targets);
  const count = groups.reduce((total, group) => total + group.keys.length, 0);
  if (count > limits.maxKeys) throw new SearchError('lookup_limit');
  if (targets.some(target => !hasLookupKeys(target)))
    throw new SearchError('missing_identity');
  const plans: { sql: string; bindings: Key }[] = [];
  for (const group of groups) {
    const chunk = Math.floor((SQL_BIND_LIMIT - 4) / group.width);
    for (let offset = 0; offset < group.keys.length; offset += chunk) {
      const keys = group.keys.slice(offset, offset + chunk);
      plans.push({ sql: `WITH requested(${group.columns}) AS (VALUES ${keys.map(() => `(${Array(group.width).fill('?').join(',')})`).join(',')}),
        candidates AS (SELECT DISTINCT q.target_id, r.revision_id, r.contribution_id, r.revision_sequence, r.contributor_id,
          r.release_id, r.artifact_schema, r.artifact_digest, r.summary_json, l.evidence_json, r.created_at
          FROM requested q ${group.join}
          CROSS JOIN releases l ON l.release_id = r.release_id
          CROSS JOIN contribution_publications p ON p.revision_id = r.revision_id
          CROSS JOIN contributors author ON author.contributor_id = r.contributor_id
          WHERE p.state = 'published' AND author.state = 'active' AND r.revision_sequence <= ? LIMIT ?),
        ranked AS (SELECT *, COUNT(*) OVER () AS candidate_count,
          ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY revision_sequence DESC) AS position FROM candidates)
        SELECT target_id, revision_id, contribution_id, revision_sequence, contributor_id, release_id, artifact_schema,
          artifact_digest, summary_json, evidence_json, created_at, candidate_count FROM ranked WHERE position <= ? LIMIT ?`,
      bindings: [...keys.flat(), ceiling, limits.maxRows + 1, limits.maxCandidates + 1, limits.maxRows + 1] });
    }
  }
  if (plans.length > limits.maxQueries) throw new SearchError('lookup_limit');
  return plans;
}

export async function searchRows(db: SqlDatabase, targets: SearchTarget[], ceiling: number, limits: SearchLimits) {
  const plans = searchPlans(targets, ceiling, limits);
  const results = await db.batch<SearchRow>(plans.map(plan => db.prepare(plan.sql).bind(...plan.bindings)));
  if (results.some(result => result.results.some(row => row.candidate_count > limits.maxRows))) throw new SearchError('lookup_limit');
  const distinct = new Map(results.flatMap(result => result.results).map(row => [`${row.target_id}:${row.revision_id}`, row]));
  if (distinct.size > limits.maxRows) throw new SearchError('lookup_limit');
  return targets.map(target => {
    const rows = [...new Map([...distinct.values()].filter(row => row.target_id === target.id)
      .map(row => [row.revision_id, row])).values()];
    return { id: target.id, rows: rows.sort((first, second) => second.revision_sequence - first.revision_sequence)
      .slice(0, limits.maxCandidates + 1) };
  });
}

export async function currentPublicState(db: SqlDatabase, ids: string[]): Promise<Map<string, PublicState>> {
  const distinct = [...new Set(ids)], statements: SqlStatement[] = [];
  if (distinct.length > 500) throw new SearchError('lookup_limit');
  for (let offset = 0; offset < distinct.length; offset += SQL_BIND_LIMIT) {
    const chunk = distinct.slice(offset, offset + SQL_BIND_LIMIT);
    statements.push(db.prepare(`SELECT r.revision_id, c.display_name, s.imports, s.likes, s.dislikes
      FROM contribution_revisions r JOIN contribution_publications p USING (revision_id)
      JOIN contributors c ON c.contributor_id = r.contributor_id JOIN contribution_statistics s USING (revision_id)
      WHERE r.revision_id IN (${chunk.map(() => '?').join(',')}) AND p.state = 'published' AND c.state = 'active'`).bind(...chunk));
  }
  if (!statements.length) return new Map();
  const results = await db.batch<PublicState>(statements);
  return new Map(results.flatMap(result => result.results).map(row => [row.revision_id, row]));
}