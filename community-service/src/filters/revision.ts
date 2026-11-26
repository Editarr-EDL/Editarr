import type { MasterFilter, SnapshotHeader } from './types';

/** A served filter in snapshot key order (§7.3). */
export function snapshotFilter(filter: MasterFilter) {
  return {
    filterId: filter.filterId, mergedIds: [...filter.mergedIds], category: filter.category,
    subcategory: filter.subcategory, severity: filter.severity, action: filter.action, expression: filter.expression,
    startMs: filter.startMs, endMs: filter.endMs, spread: { startMs: filter.spread.startMs, endMs: filter.spread.endMs },
    confidence: filter.confidence, contributorCount: filter.contributorCount,
    support: { attestations: filter.support.attestations, upvotes: filter.support.upvotes },
    negative: { downvotes: filter.negative.downvotes, disputes: filter.negative.disputes,
      coverageNegatives: filter.negative.coverageNegatives },
    lowConfidence: filter.lowConfidence,
  };
}

export const SNAPSHOT_SCHEMA_VERSION = 'community-master-snapshot-1';
/** Maximum snapshot size in bytes (§7.3). */
export const SNAPSHOT_MAX_BYTES = 2_000_000;

/**
 * Canonical served content (§6.9): every snapshot field except `revision` and
 * `generatedAt`, in snapshot key order.
 */
export function servedContent(versionId: string, header: SnapshotHeader, runtimeMs: number | null, trust: number,
  servedFilters: readonly MasterFilter[]) {
  const { work, versionInfo } = header;
  return JSON.stringify({
    schemaVersion: SNAPSHOT_SCHEMA_VERSION, service: header.service, versionId,
    work: { workId: work.workId, kind: work.kind, tmdb: work.tmdb, tvdb: work.tvdb, season: work.season,
      episode: work.episode, lastEpisode: work.lastEpisode, title: work.title, year: work.year },
    versionInfo: { status: versionInfo.status, aliasId: versionInfo.aliasId, label: versionInfo.label,
      labelText: versionInfo.labelText, runtimeMs, trust },
    filters: servedFilters.map(snapshotFilter),
  });
}

/** Throw `snapshot_too_large` when snapshot bytes exceed `SNAPSHOT_MAX_BYTES`. */
export function assertSnapshotSize(bytes: Uint8Array) {
  if (bytes.byteLength > SNAPSHOT_MAX_BYTES) throw new Error('snapshot_too_large');
  return bytes;
}

/**
 * Build the exact snapshot bytes for a revision from `MergeResult.servedContent`,
 * inserting `revision` and `generatedAt` after `versionId`. Enforces the size limit;
 * the snapshot writer must use this before storing or signing a revision.
 */
export function snapshotBytes(content: string, revision: number, generatedAt: number) {
  const { schemaVersion, service, versionId, ...rest } = JSON.parse(content) as Record<string, unknown>;
  const json = JSON.stringify({ schemaVersion, service, versionId, revision, generatedAt, ...rest });
  return assertSnapshotSize(new TextEncoder().encode(json));
}

/**
 * Advance the revision only when served content changed (§6.9). A version without
 * a stored snapshot keeps its creation revision (default 1) and reports a change.
 * A quarantined version keeps its current revision and content.
 */
export function nextRevision(previous: { revision: number; servedContent: string | null } | null | undefined, content: string,
  quarantined = false) {
  if (!previous) return { revision: 1, changed: !quarantined };
  if (quarantined) return { revision: previous.revision, changed: false };
  if (previous.servedContent === null) return { revision: previous.revision, changed: true };
  if (previous.servedContent === content) return { revision: previous.revision, changed: false };
  return { revision: previous.revision + 1, changed: true };
}
