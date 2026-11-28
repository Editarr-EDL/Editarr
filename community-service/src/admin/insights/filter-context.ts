import type { SqlDatabase } from '../../runtime/contracts';
import { resolveLabels } from '../labels';
import { filterDetail, filterDetailColumns } from './version-detail';
import { InsightNotFound, InsightQueryError, ms } from './query';

const LIMIT = 200;

/**
 * `GET /admin/v1/filters/{filterId}/context`: the filter with its item and version, current votes
 * (with the receipt revision they were cast against), attestations, reports and moderation history
 * of the filter and its votes. Lists are bounded to 200 rows (20 for moderation).
 */
export async function filterContext(db: SqlDatabase, url: URL, filterId: string, now: number) {
  if (url.search) throw new InsightQueryError('invalid_filter_query');
  const [filter, votes, attestations, reports, moderation] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT ${filterDetailColumns} FROM filter_registry r LEFT JOIN master_filters f ON f.filter_id = r.filter_id
      WHERE r.filter_id = ?`).bind(filterId),
    db.prepare(`SELECT v.contributor_id AS contributorId, v.vote, v.excluded, d.revision, v.updated_at AS at,
      coalesce((SELECT revision FROM moderation_versions WHERE resource = 'vote:' || v.filter_id || ':' || v.contributor_id), 0) AS moderationRevision
      FROM filter_votes v LEFT JOIN download_receipts d ON d.receipt_id = v.receipt_id WHERE v.filter_id = ?
      ORDER BY v.updated_at DESC, v.contributor_id LIMIT ${LIMIT}`).bind(filterId),
    db.prepare(`SELECT a.contributor_id AS contributorId, a.submission_id AS submissionId, a.entry_id AS entryId, a.kind, a.implicit,
      a.counted, va.tier, va.accepted_at AS at, e.start_ms AS startMs, e.end_ms AS endMs, e.aligned_start_ms AS alignedStartMs,
      e.aligned_end_ms AS alignedEndMs, e.origin, e.manual_review AS manualReview, e.locally_verified AS locallyVerified,
      e.model_version AS modelVersion, e.ignored_reason AS ignoredReason, coalesce(e.quarantined, 0) AS quarantined
      FROM filter_attestations a LEFT JOIN submission_entries e ON e.submission_id = a.submission_id AND e.entry_id = a.entry_id
      LEFT JOIN version_acceptances va ON va.submission_id = a.submission_id WHERE a.filter_id = ?
      ORDER BY va.accepted_at DESC, a.submission_id, a.entry_id LIMIT ${LIMIT}`).bind(filterId),
    db.prepare(`SELECT report_id AS reportId, contributor_id AS contributorId, reason, note, state, created_at AS at,
      coalesce((SELECT revision FROM moderation_versions WHERE resource = 'report:' || report_id), 0) AS moderationRevision
      FROM reports WHERE target_type = 'filter' AND target_id = ? ORDER BY created_at DESC, report_id LIMIT ${LIMIT}`).bind(filterId),
    db.prepare(`SELECT action_id AS actionId, actor, resource, command, reason, outcome, created_at AS at FROM moderation_actions
      WHERE resource = 'filter:' || ?1 OR (resource >= 'vote:' || ?1 || ':' AND resource < 'vote:' || ?1 || ';')
      ORDER BY created_at DESC, action_id DESC LIMIT 20`).bind(filterId)]);
  if (!filter.results.length) throw new InsightNotFound();
  const voters = moderation.results.map(row => row.resource.startsWith('vote:') ? row.resource.split(':')[2] : null);
  const labels = await resolveLabels(db, { filters: [filterId], contributors: [...votes.results, ...attestations.results,
    ...reports.results].map(row => row.contributorId).concat(voters) });
  const { item, version } = labels.target('filter', filterId);
  const who = (id: string) => labels.contributor(id)!;
  return {
    version: 1, observedAt: now * 1000, filter: filterDetail(filter.results[0]), item, versionLabel: version,
    votes: votes.results.map(row => ({ contributor: who(row.contributorId), vote: row.vote, excluded: row.excluded === 1,
      revision: row.revision, at: ms(row.at), moderation: { resource: `vote:${filterId}:${row.contributorId}`, revision: row.moderationRevision } })),
    attestations: attestations.results.map(row => ({ contributor: who(row.contributorId), submissionId: row.submissionId,
      entryId: row.entryId, kind: row.kind, implicit: row.implicit === 1, counted: row.counted === 1, tier: row.tier, at: ms(row.at),
      startMs: row.startMs, endMs: row.endMs, alignedStartMs: row.alignedStartMs, alignedEndMs: row.alignedEndMs, origin: row.origin,
      manualReview: row.manualReview === null ? null : row.manualReview === 1,
      locallyVerified: row.locallyVerified === null ? null : row.locallyVerified === 1, modelVersion: row.modelVersion,
      ignoredReason: row.ignoredReason, quarantined: row.quarantined === 1 })),
    reports: reports.results.map(row => ({ reportId: row.reportId, contributor: who(row.contributorId), reason: row.reason,
      note: row.note, state: row.state, at: ms(row.at), moderation: { resource: `report:${row.reportId}`, revision: row.moderationRevision } })),
    moderation: moderation.results.map((row, index) => ({ actionId: row.actionId, actor: row.actor, resource: row.resource,
      command: row.command, reason: row.reason, outcome: row.outcome, at: row.at, voter: labels.contributor(voters[index]) })),
  };
}
