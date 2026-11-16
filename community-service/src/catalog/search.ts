import type { SqlDatabase } from '../runtime/contracts';
import { BANDS, POLICY_VERSION, matchEvidenceSchema, type Compatibility } from './compatibility-inputs';
import { classify, compatibilityCacheKey } from './compatibility';
import { sha256 } from './records';
import { currentPublicState, hasLookupKeys, searchPlans, searchRows, type SearchRow } from './search-repository';
import { SearchError, type SearchLimits, type SearchTarget } from './search-input';
import { rankRevision, RANKING_POLICY_VERSION, SCORE_SCALE, type Ranking } from '../reputation/ranking';

type Rows = { id: string; rows: SearchRow[] }[];
type Entry = { expires: number; bytes: number; rows: Rows };
const caches = new WeakMap<object, Map<string, Entry>>();
const encoder = new TextEncoder();
const cursorVersion = 'community-search-2';
const schemas = ['community-artifact-1', 'community-artifact-2'];

export interface Candidate {
  contributionId: string; revisionId: string; artifactDigest: string; artifactSchema: string; releaseId: string; catalogCreatedAt: number;
  attribution: { contributorId: string; displayName: string };
  summary: Record<string, unknown>; evidence: SearchTarget['evidence']; compatibility: Compatibility;
  statistics: { imports: number; likes: number; dislikes: number }; ranking: Ranking;
}

async function queryKey(targets: SearchTarget[], policy: string, limits: SearchLimits, epoch: number) {
  return sha256(encoder.encode(JSON.stringify([cursorVersion, schemas, POLICY_VERSION, RANKING_POLICY_VERSION, policy,
    limits.maxCandidates, limits.maxRows, epoch, targets.map(target => [target.id, compatibilityCacheKey(target.evidence, target.evidence)])])));
}

async function cachedRows(db: SqlDatabase, targets: SearchTarget[], ceiling: number,
  key: string, now: number, refresh: boolean, limits: SearchLimits, cacheScope: object) {
  searchPlans(targets, ceiling, limits);
  let cache = caches.get(cacheScope);
  if (!cache) { cache = new Map(); caches.set(cacheScope, cache); }
  for (const [name, entry] of cache) if (entry.expires <= now) cache.delete(name);
  const cacheKey = `${key}:${ceiling}`;
  const cached = cache.get(cacheKey);
  if (!refresh && cached) return cached.rows;
  const rows = await searchRows(db, targets, ceiling, limits);
  const bytes = encoder.encode(JSON.stringify(rows)).length;
  cache.delete(cacheKey);
  while (cache.size && (cache.size >= limits.cacheEntries ||
      [...cache.values()].reduce((total, entry) => total + entry.bytes, 0) + bytes > limits.cacheBytes))
    cache.delete(cache.keys().next().value!);
  if (bytes <= limits.cacheBytes) cache.set(cacheKey, { rows, bytes,
    expires: now + (rows.some(item => item.rows.length) ? limits.positiveTtlSeconds : limits.negativeTtlSeconds) });
  return rows;
}

async function catalogSnapshot(db: SqlDatabase) {
  const row = await db.prepare(`SELECT COALESCE(MAX(revision_sequence), 0) AS sequence,
    (SELECT epoch FROM moderation_epoch WHERE singleton = 1) AS epoch FROM contribution_revisions`)
    .first<{ sequence: number; epoch: number }>();
  if (!row || !Number.isSafeInteger(row.epoch) || row.epoch < 0) throw new Error('catalog_unavailable');
  return row;
}

export async function catalogCeiling(db: SqlDatabase) { return (await catalogSnapshot(db)).sequence; }

const summaryFields = ['schema_version', 'registry_version', 'submission_id', 'kind', 'work_ids', 'release',
  'duration_seconds', 'release_version', 'scanner_behavior_version', 'total', 'sources', 'channels', 'taxonomy',
  'coverage', 'name', 'matching', 'audio_association', 'source_revision', 'analyzed_audio', 'source_states'];
function publicSummary(value: string): Record<string, unknown> {
  const summary = JSON.parse(value) as Record<string, unknown>;
  if (!['community-summary-1', 'community-summary-2'].includes(String(summary.schema_version)))
    throw new Error('catalog_unavailable');
  return Object.fromEntries(summaryFields.filter(field => Object.hasOwn(summary, field)).map(field => [field, summary[field]]));
}
type Position = [number, number, number, string];
type RankedCandidate = Pick<Candidate, 'compatibility' | 'ranking' | 'revisionId'>;
const position = (candidate: RankedCandidate): Position =>
  [BANDS.indexOf(candidate.compatibility.video.band), BANDS.indexOf(candidate.compatibility.audio.band),
    -Math.round(candidate.ranking.score * SCORE_SCALE), candidate.revisionId];
const compare = (first: Position, second: Position) =>
  first[0] - second[0] || first[1] - second[1] || first[2] - second[2] ||
  (first[3] < second[3] ? -1 : first[3] > second[3] ? 1 : 0);

export function compareCandidates(first: RankedCandidate, second: RankedCandidate) {
  return compare(position(first), position(second));
}

async function candidates(db: SqlDatabase, targets: SearchTarget[], rows: Rows) {
  const state = await currentPublicState(db, rows.flatMap(item => item.rows.map(row => row.revision_id)));
  return targets.map(target => {
    const source = rows.find(item => item.id === target.id)!;
    const items: Candidate[] = source.rows.filter(row => state.has(row.revision_id)).map(row => {
      const current = state.get(row.revision_id)!;
      const evidence = matchEvidenceSchema.parse(JSON.parse(row.evidence_json));
      return { contributionId: row.contribution_id, revisionId: row.revision_id, artifactSchema: row.artifact_schema, artifactDigest: row.artifact_digest,
        releaseId: row.release_id, catalogCreatedAt: row.created_at,
        attribution: { contributorId: row.contributor_id, displayName: current.display_name },
        summary: publicSummary(row.summary_json), evidence, compatibility: classify(target.evidence, evidence),
        statistics: { imports: current.imports, likes: current.likes, dislikes: current.dislikes },
        ranking: rankRevision(current.likes, current.dislikes) };
    });
    return { id: target.id, items: items.sort(compareCandidates),
      retrieved: source.rows.length };
  });
}

export async function availability(db: SqlDatabase, targets: SearchTarget[], policy: string,
  now: number, refresh: boolean, limits: SearchLimits, cacheScope: object = db) {
  const searchable = targets.filter(hasLookupKeys);
  const snapshot = await catalogSnapshot(db), ceiling = snapshot.sequence, key = await queryKey(searchable, policy, limits, snapshot.epoch);
  const rows = searchable.length ? await cachedRows(db, searchable, ceiling, key, now, refresh, limits, cacheScope) : [];
  const results = await candidates(db, searchable, rows);
  return { version: 1, policyVersion: POLICY_VERSION, items: targets.map(target => {
    const result = results.find(item => item.id === target.id);
    if (!result) return { id: target.id, status: 'missing_identity', candidateCount: null, truncated: false, channels: null };
    const visible = result.items.slice(0, limits.maxCandidates);
    const channels = Object.fromEntries((['audio', 'video'] as const).map(channel => {
      const counts = Object.fromEntries(BANDS.map(band => [band, 0]));
      for (const candidate of visible) {
        const totals = candidate.summary.channels as Record<string, number>;
        if ((totals?.[channel] ?? 0) > 0) counts[candidate.compatibility[channel].band]! += 1;
      }
      return [channel, counts];
    }));
    return { id: result.id, status: 'ok', candidateCount: visible.length,
      truncated: result.retrieved > limits.maxCandidates, channels };
  }) };
}

export async function matches(db: SqlDatabase, target: SearchTarget, pageSize: number,
  cursor: string | null, policy: string, now: number, refresh: boolean, limits: SearchLimits, cacheScope: object = db) {
  const catalog = await catalogSnapshot(db), latest = catalog.sequence, key = await queryKey([target], policy, limits, catalog.epoch);
  let ceiling = latest, after: Position | null = null, expectedSnapshot: string | null = null;
  if (cursor) {
    try {
      const value = JSON.parse(atob(cursor));
      if (Object.keys(value).sort().join(',') !== 'after,ceiling,key,snapshot,version' || value.version !== cursorVersion || value.key !== key ||
          !Number.isSafeInteger(value.ceiling) || value.ceiling < 0 || value.ceiling > latest ||
          !Array.isArray(value.after) || value.after.length !== 4 ||
          value.after.slice(0, 2).some((rank: unknown) => !Number.isInteger(rank) || Number(rank) < 0 || Number(rank) >= BANDS.length) ||
            !Number.isSafeInteger(value.after[2]) || value.after[2] < -SCORE_SCALE || value.after[2] > 0 ||
            typeof value.after[3] !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(value.after[3]) ||
            typeof value.snapshot !== 'string' || !/^[a-f0-9]{64}$/.test(value.snapshot)) throw new Error('invalid');
          ceiling = value.ceiling; after = value.after; expectedSnapshot = value.snapshot;
    } catch { throw new SearchError('invalid_cursor'); }
  }
  const rows = await cachedRows(db, [target], ceiling, key, now, refresh, limits, cacheScope);
  if (rows[0].rows.length > limits.maxCandidates) throw new SearchError('lookup_limit');
  const result = (await candidates(db, [target], rows))[0];
  const snapshot = await sha256(encoder.encode(JSON.stringify(result.items.map(item =>
    [item.revisionId, item.statistics.likes, item.statistics.dislikes]))));
  if (expectedSnapshot !== null && expectedSnapshot !== snapshot) throw new SearchError('invalid_cursor');
  const remaining = result.items.filter(item => !after || compare(position(item), after) > 0);
  const page = remaining.slice(0, pageSize);
  const nextCursor = remaining.length > pageSize ? btoa(JSON.stringify({ version: cursorVersion, key, ceiling, snapshot,
    after: position(page[page.length - 1]) })) : null;
  return { version: 1, policyVersion: POLICY_VERSION, candidates: page, nextCursor };
}