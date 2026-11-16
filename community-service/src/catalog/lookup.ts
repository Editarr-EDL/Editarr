import type { SqlDatabase } from '../runtime/contracts';
import { matchEvidenceSchema, type EpisodeNumber, type Identifier } from './compatibility-inputs';
import { identifier, positive, SQL_BIND_LIMIT } from './records';

export interface LookupLimits { maxKeys: number; maxResults: number; maxQueries: number; beforeSequence?: number }
export interface CatalogRow {
  revision_id: string; revision_sequence: number; contributor_id: string; release_id: string;
  artifact_schema: string; artifact_digest: string; summary_json: string; evidence_json: string;
}
type Query = { sql: string; bindings: (string | number)[] };
const columns = `r.revision_id, r.revision_sequence, r.contributor_id, r.release_id,
  r.artifact_schema, r.artifact_digest, r.summary_json, l.evidence_json`;
const published = `CROSS JOIN releases l ON l.release_id = r.release_id
  CROSS JOIN contribution_publications p ON p.revision_id = r.revision_id
  CROSS JOIN contributors author ON author.contributor_id = r.contributor_id
  WHERE p.state = 'published' AND author.state = 'active' AND r.revision_sequence < ? ORDER BY r.revision_sequence DESC LIMIT ?`;
function checkBatch(count: number, width: number) {
  if (count < 1 || count * width + 2 > SQL_BIND_LIMIT) throw new Error('invalid_lookup_batch');
}

export function identifierQuery(keys: Identifier[], limit: number, beforeSequence = Number.MAX_SAFE_INTEGER): Query {
  checkBatch(keys.length, 3);
  const items = keys.map(identifier);
  return { sql: `WITH requested(provider, entity_type, value) AS (VALUES ${items.map(() => '(?,?,?)').join(',')})
    SELECT DISTINCT ${columns} FROM requested q
    CROSS JOIN work_identifiers w ON w.provider = q.provider AND w.entity_type = q.entity_type AND w.value = q.value
    CROSS JOIN release_work_associations a ON a.work_id = w.work_id
    CROSS JOIN contribution_revisions r ON r.release_id = a.release_id ${published}`,
  bindings: [...items.flatMap(item => [item.provider, item.entity_type, item.value]), positive(beforeSequence), positive(limit)] };
}
export interface FingerprintKey { channel: 'file' | 'audio' | 'video'; algorithm: string; digest: string }
export function fingerprintQuery(keys: FingerprintKey[], limit: number, beforeSequence = Number.MAX_SAFE_INTEGER): Query {
  checkBatch(keys.length, 3);
  for (const item of keys) {
    if (!['file', 'audio', 'video'].includes(item.channel) || !/^[A-Za-z0-9_-]{1,128}$/.test(item.algorithm) ||
      !/^[a-f0-9]{1,128}$/.test(item.digest)) throw new Error('invalid_fingerprint_key');
  }
  return { sql: `WITH requested(channel, algorithm, digest) AS (VALUES ${keys.map(() => '(?,?,?)').join(',')})
    SELECT DISTINCT ${columns} FROM requested q
    CROSS JOIN release_fingerprints f ON f.channel = q.channel AND f.algorithm = q.algorithm AND f.digest = q.digest
    CROSS JOIN contribution_revisions r ON r.release_id = f.release_id ${published}`,
  bindings: [...keys.flatMap(item => [item.channel, item.algorithm, item.digest]), positive(beforeSequence), positive(limit)] };
}
export interface EpisodeKey { series: Identifier; number: EpisodeNumber }
export function episodeQuery(keys: EpisodeKey[], limit: number, beforeSequence = Number.MAX_SAFE_INTEGER): Query {
  checkBatch(keys.length, 5);
  for (const item of keys) matchEvidenceSchema.parse({ media: { entity_type: 'episode', identifiers: [],
    series_identifiers: [item.series], episode_numbers: [item.number], release: {} } });
  return { sql: `WITH requested(provider, value, scheme, season, number) AS (VALUES ${keys.map(() => '(?,?,?,?,?)').join(',')})
    SELECT DISTINCT ${columns} FROM requested q
    CROSS JOIN work_identifiers w ON w.provider = q.provider AND w.entity_type = 'series' AND w.value = q.value
    CROSS JOIN release_episodes e ON e.series_work_id = w.work_id AND e.scheme = q.scheme AND e.season = q.season AND e.episode_number = q.number
    CROSS JOIN contribution_revisions r ON r.release_id = e.release_id ${published}`,
  bindings: [...keys.flatMap(item => [item.series.provider, item.series.value, item.number.scheme,
    item.number.season ?? -1, item.number.number]), positive(beforeSequence), positive(limit)] };
}

async function lookup<T>(db: SqlDatabase, keys: T[], width: number,
  query: (keys: T[], limit: number, beforeSequence?: number) => Query, limits: LookupLimits): Promise<CatalogRow[]> {
  positive(limits.maxKeys); positive(limits.maxResults); positive(limits.maxQueries);
  const chunkSize = Math.floor((SQL_BIND_LIMIT - 2) / width);
  if (keys.length > limits.maxKeys || Math.ceil(keys.length / chunkSize) > limits.maxQueries)
    throw new Error('catalog_lookup_limit');
  let rows: CatalogRow[] = [];
  for (let offset = 0; offset < keys.length; offset += chunkSize) {
    const plan = query(keys.slice(offset, offset + chunkSize), limits.maxResults, limits.beforeSequence);
    const result = await db.prepare(plan.sql).bind(...plan.bindings).all<CatalogRow>();
    rows = [...new Map([...rows, ...result.results].map(item => [item.revision_id, item])).values()]
      .sort((first, second) => second.revision_sequence - first.revision_sequence).slice(0, limits.maxResults);
  }
  return rows;
}
export const lookupIdentifiers = (db: SqlDatabase, keys: Identifier[], limits: LookupLimits) => lookup(db, keys, 3, identifierQuery, limits);
export const lookupFingerprints = (db: SqlDatabase, keys: FingerprintKey[], limits: LookupLimits) => lookup(db, keys, 3, fingerprintQuery, limits);
export const lookupEpisodes = (db: SqlDatabase, keys: EpisodeKey[], limits: LookupLimits) => lookup(db, keys, 5, episodeQuery, limits);