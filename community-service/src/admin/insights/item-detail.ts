import type { SqlDatabase } from '../../runtime/contracts';
import { itemColumns, itemFromRow, versionLabelSql } from '../labels';
import { InsightNotFound, InsightQueryError, ms } from './query';

const MAX_VERSIONS = 100;

/**
 * `GET /admin/v1/items/{workId}`: the work's titles and identifiers and every version (merged ones
 * included, with `mergedInto`) with trust, match-key evidence (no key values), downloads, uploads,
 * contributors, filter counts, open reports and the moderation token for version actions.
 */
export async function itemDetail(db: SqlDatabase, url: URL, workId: string, now: number) {
  if (url.search) throw new InsightQueryError('invalid_item_query');
  const [work, titles, identifiers, versions, keys, contributors] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT ${itemColumns('w')}, w.created_at AS createdAt FROM works w WHERE w.work_id = ?`).bind(workId),
    db.prepare(`SELECT title, nullif(year, -1) AS year, observations FROM work_titles WHERE work_id = ?
      ORDER BY observations DESC, title LIMIT 20`).bind(workId),
    db.prepare(`SELECT provider, kind, value, nullif(season, -1) AS season, nullif(episode, -1) AS episode,
      nullif(last_episode, -1) AS lastEpisode FROM work_identifiers WHERE work_id = ? ORDER BY provider, value LIMIT 20`).bind(workId),
    db.prepare(`SELECT v.version_id AS versionId, ${versionLabelSql('v')} AS label, v.alias_id AS aliasId, v.label_text AS labelText,
      v.status, v.merged_into AS mergedInto, v.quarantined, v.runtime_ms AS runtimeMs, v.trust, v.revision, v.created_at AS createdAt,
      (SELECT generated_at FROM master_revisions m WHERE m.version_id = v.version_id ORDER BY revision DESC LIMIT 1) AS revisedAt,
      (SELECT count(*) FROM download_receipts d WHERE d.version_id = v.version_id) AS downloads,
      (SELECT count(*) FROM version_acceptances a WHERE a.version_id = v.version_id) AS uploads,
      (SELECT count(*) FROM master_filters f WHERE f.version_id = v.version_id) AS filters,
      (SELECT count(*) FROM master_filters f WHERE f.version_id = v.version_id AND f.served = 1) AS servedFilters,
      (SELECT count(*) FROM reports r WHERE r.state = 'open' AND ((r.target_type = 'version' AND r.target_id = v.version_id)
        OR (r.target_type = 'filter' AND r.target_id IN (SELECT filter_id FROM filter_registry WHERE version_id = v.version_id)))) AS openReports,
      coalesce((SELECT revision FROM moderation_versions WHERE resource = 'version:' || v.version_id), 0) AS moderationRevision
      FROM versions v WHERE v.work_id = ? ORDER BY v.created_seq LIMIT ${MAX_VERSIONS}`).bind(workId),
    db.prepare(`SELECT k.version_id AS versionId, k.type, k.algorithm, k.size_bytes AS sizeBytes, k.is_reference AS isReference,
      k.observations FROM versions v JOIN version_match_keys k ON k.version_id = v.version_id WHERE v.work_id = ?
      ORDER BY k.version_id, k.is_reference DESC, k.type LIMIT 1000`).bind(workId),
    db.prepare(`SELECT c.version_id AS versionId, c.contributor_id AS contributorId,
      coalesce(p.display_name, c.contributor_id) AS displayName, c.tier, c.founder
      FROM versions v JOIN version_counted_acceptances c ON c.version_id = v.version_id
      LEFT JOIN contributors p ON p.contributor_id = c.contributor_id WHERE v.work_id = ? ORDER BY c.version_id, c.accepted_seq LIMIT 1000`)
      .bind(workId)]);
  if (!work.results.length) throw new InsightNotFound();
  const row = work.results[0];
  const group = (rows: Record<string, any>[], versionId: string) => rows.filter(entry => entry.versionId === versionId)
    .map(({ versionId: _, ...rest }) => rest);
  return {
    version: 1, observedAt: now * 1000,
    work: { ...itemFromRow(row), createdAt: ms(row.createdAt), aliases: titles.results.slice(1),
      identifiers: identifiers.results },
    versions: versions.results.map(({ moderationRevision, ...version }) => ({
      ...version, quarantined: version.quarantined === 1, createdAt: ms(version.createdAt), revisedAt: ms(version.revisedAt),
      matchKeys: group(keys.results, version.versionId).map(key => ({ ...key, isReference: key.isReference === 1 })),
      contributors: group(contributors.results, version.versionId).map(entry => ({ contributorId: entry.contributorId,
        displayName: entry.displayName, tier: entry.tier, founder: entry.founder === 1 })),
      moderation: { resource: `version:${version.versionId}`, revision: moderationRevision },
    })),
  };
}
