import type { SqlDatabase } from '../runtime/contracts';
import { storedKey, identifierRow, type EvidenceKey, type EvidenceKeyType } from '../versions/keys';
import { displayLabels, resolveText, spellingLookup, type Label } from '../versions/labels';
import { normalize } from '../versions/normalize';
import { workSummaries, type WorkSummary } from '../versions/work-summary';
import { compareMatches, scoreVersion, type LadderScore, type RankedMatch, type VersionEvidence } from './ladder';
import type { MatchRequest } from './input';

export interface MatchResult extends LadderScore {
  versionId: string; status: 'confirmed' | 'candidate'; aliasId: string | null; label: string | null;
  labelText: string | null; work: WorkSummary; runtimeMs: number | null; trust: number; revision: number;
  filterCount: number; contributorCount: number;
}
/** Served filter counts at each version's current revision, supplied by the master-set owner. */
export type FilterCounts = (db: SqlDatabase, versionIds: readonly string[]) => Promise<Map<string, number>>;
export interface MatchOptions { filterCounts?: FilterCounts }

const noFilters: FilterCounts = async () => new Map();
const EVIDENCE = new Set<string>(['sha256_full', 'sha256_sampled', 'md5_quick', 'audio_stream', 'video_stream']);

async function queryLabel(db: SqlDatabase, label: MatchRequest['label']): Promise<Label> {
  if (label === null) return { aliasId: null, labelText: null };
  if ('aliasId' in label) return { aliasId: label.aliasId, labelText: null };
  return resolveText(label.text, spellingLookup(db));
}

type Evidence = { evidenceTypes: Set<EvidenceKeyType>; providerTypes: Set<'tmdb' | 'tvdb'>; titleMatched: boolean };

/**
 * Score every non-merged, unquarantined version that shares a key with the query on the §4
 * ladder, then order and truncate to `limit`.
 */
export async function matchVersions(db: SqlDatabase, query: MatchRequest, options: MatchOptions = {}): Promise<MatchResult[]> {
  const byVersion = new Map<string, Evidence>(), providerWorks = new Map<string, Set<'tmdb' | 'tvdb'>>();
  const titleWorks = new Set<string>();
  const evidence = (versionId: string) => byVersion.get(versionId) ??
    byVersion.set(versionId, { evidenceTypes: new Set(), providerTypes: new Set(), titleMatched: false }).get(versionId)!;
  for (const key of query.keys) {
    if (EVIDENCE.has(key.type)) {
      const stored = storedKey(key as EvidenceKey);
      const rows = await db.prepare(`SELECT version_id FROM version_match_keys
        WHERE type = ? AND algorithm = ? AND value = ? AND size_bytes = ?`)
        .bind(stored.type, stored.algorithm, stored.value, stored.sizeBytes).all<{ version_id: string }>();
      for (const row of rows.results) evidence(row.version_id).evidenceTypes.add(stored.type);
    } else if (key.type === 'tmdb' || key.type === 'tvdb') {
      const row = key.kind === 'movie' ? identifierRow(key.type, 'movie', key.id)
        : identifierRow(key.type, 'episode', key.id, key.season, key.episode, key.lastEpisode ?? null);
      const rows = await db.prepare(`SELECT work_id FROM work_identifiers WHERE provider = ? AND kind = ? AND value = ?
        AND season = ? AND episode = ? AND last_episode = ?`)
        .bind(row.provider, row.kind, row.value, row.season, row.episode, row.lastEpisode).all<{ work_id: string }>();
      for (const { work_id } of rows.results) providerWorks.set(work_id, (providerWorks.get(work_id) ?? new Set()).add(key.type));
    } else if (key.type === 'title_year' && key.year !== null) {
      const rows = await db.prepare('SELECT work_id FROM work_titles WHERE normalized = ? AND year = ?')
        .bind(normalize(key.title), key.year).all<{ work_id: string }>();
      for (const { work_id } of rows.results) titleWorks.add(work_id);
    }
  }
  const works = [...new Set([...providerWorks.keys(), ...titleWorks])];
  const versions = await db.prepare(`SELECT version_id, work_id, alias_id, label_text, status, runtime_ms, trust, revision
    FROM versions WHERE status != 'merged_into' AND quarantined = 0
      AND (version_id IN (SELECT value FROM json_each(?)) OR work_id IN (SELECT value FROM json_each(?)))`)
    .bind(JSON.stringify([...byVersion.keys()]), JSON.stringify(works)).all<{ version_id: string; work_id: string;
      alias_id: string | null; label_text: string | null; status: 'confirmed' | 'candidate'; runtime_ms: number | null;
      trust: number; revision: number }>();
  const label = await queryLabel(db, query.label);
  const scored: (RankedMatch & { row: typeof versions.results[number] })[] = [];
  for (const row of versions.results) {
    const found = evidence(row.version_id);
    for (const type of providerWorks.get(row.work_id) ?? []) found.providerTypes.add(type);
    found.titleMatched = titleWorks.has(row.work_id);
    const score = scoreVersion(found as VersionEvidence, { label: { aliasId: row.alias_id, labelText: row.label_text },
      runtimeMs: row.runtime_ms }, label, query.runtimeMs);
    if (score) scored.push({ ...score, versionId: row.version_id, status: row.status, trust: row.trust, row });
  }
  const top = scored.sort(compareMatches).slice(0, query.limit);
  if (!top.length) return [];
  const ids = top.map(item => item.versionId);
  const [summaries, labels, filters, contributors] = await Promise.all([
    workSummaries(db, top.map(item => item.row.work_id)), displayLabels(db),
    (options.filterCounts ?? noFilters)(db, ids), contributorCounts(db, ids)]);
  return top.map(({ row, ...score }) => ({ versionId: row.version_id, status: row.status, aliasId: row.alias_id,
    label: row.alias_id !== null ? labels.get(row.alias_id) ?? null : row.label_text, labelText: row.label_text,
    work: summaries.get(row.work_id)!, tier: score.tier, matchPercent: score.matchPercent, matchedKeys: score.matchedKeys,
    runtimeAgreement: score.runtimeAgreement, runtimeMs: row.runtime_ms, trust: row.trust, revision: row.revision,
    filterCount: filters.get(row.version_id) ?? 0, contributorCount: contributors.get(row.version_id) ?? 0 }));
}

/** Distinct contributors with a counted submission per version. */
export async function contributorCounts(db: SqlDatabase, versionIds: readonly string[]): Promise<Map<string, number>> {
  const rows = await db.prepare(`SELECT version_id, count(DISTINCT contributor_id) AS total FROM version_counted_acceptances
    WHERE version_id IN (SELECT value FROM json_each(?)) GROUP BY version_id`).bind(JSON.stringify(versionIds))
    .all<{ version_id: string; total: number }>();
  return new Map(rows.results.map(row => [row.version_id, row.total]));
}
