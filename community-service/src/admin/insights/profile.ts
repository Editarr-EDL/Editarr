import type { SqlDatabase } from '../../runtime/contracts';
import { usageStatus } from '../../budgets/usage';
import { resolveLabels } from '../labels';
import { InsightNotFound, InsightQueryError, ms } from './query';

const RECENT = 10;

/**
 * `GET /admin/v1/contributors/{id}/profile`: identity (no Discord IDs or keys), installations with
 * key counts, newest key and last sync, recent uploads, vote history, reports filed and against
 * (filters the contributor supports), totals and today's application-observed usage.
 */
export async function contributorProfile(db: SqlDatabase, url: URL, contributorId: string, now: number) {
  if (url.search) throw new InsightQueryError('invalid_profile_query');
  const [identity, membership, installations, uploads, votes, filed, against, totals, storage] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT contributor_id AS contributorId, display_name AS displayName, state, quarantined, created_at AS joinedAt,
      profile_updated_at AS profileUpdatedAt,
      coalesce((SELECT revision FROM moderation_versions WHERE resource = 'contributor:' || contributor_id), 0) AS moderationRevision
      FROM contributors WHERE contributor_id = ?`).bind(contributorId),
    db.prepare(`SELECT authority, state, checked_at AS checkedAt FROM discord_membership WHERE contributor_id = ? ORDER BY authority LIMIT 10`)
      .bind(contributorId),
    db.prepare(`SELECT i.installation_id AS installationId, i.created_at AS createdAt,
      (SELECT count(*) FROM installation_keys k WHERE k.installation_id = i.installation_id AND k.state = 'active') AS activeKeys,
      (SELECT count(*) FROM installation_keys k WHERE k.installation_id = i.installation_id AND k.state = 'retired') AS retiredKeys,
      (SELECT count(*) FROM installation_keys k WHERE k.installation_id = i.installation_id AND k.state = 'revoked') AS revokedKeys,
      (SELECT json_object('keyId', k.key_id, 'state', k.state, 'createdAt', k.created_at, 'retiredAt', k.retired_at)
        FROM installation_keys k WHERE k.installation_id = i.installation_id ORDER BY k.created_at DESC, k.key_id DESC LIMIT 1) AS newestKey,
      (SELECT max(started_at) FROM sync_runs s WHERE s.installation_id = i.installation_id) AS lastSyncAt
      FROM installations i WHERE i.contributor_id = ? ORDER BY i.created_at DESC, i.installation_id LIMIT 20`).bind(contributorId),
    db.prepare(`SELECT a.submission_id AS submissionId, a.version_id AS versionId, a.accepted_at AS at, a.tier, a.quarantined,
      (SELECT state FROM submissions s WHERE s.submission_id = a.submission_id) AS state,
      (SELECT count(*) FROM submission_entries e WHERE e.submission_id = a.submission_id) AS entries
      FROM version_acceptances a WHERE a.contributor_id = ? ORDER BY a.accepted_at DESC, a.accepted_seq DESC LIMIT ${RECENT}`).bind(contributorId),
    db.prepare(`SELECT event_id AS eventId, filter_id AS filterId, vote, kind, created_at AS at FROM vote_events
      WHERE contributor_id = ? ORDER BY created_at DESC, event_id DESC LIMIT ${RECENT}`).bind(contributorId),
    db.prepare(`SELECT report_id AS reportId, target_type AS targetType, target_id AS targetId, reason, state, created_at AS at
      FROM reports WHERE contributor_id = ? ORDER BY created_at DESC, report_id LIMIT ${RECENT}`).bind(contributorId),
    db.prepare(`SELECT r.report_id AS reportId, r.contributor_id AS reporterId, r.target_id AS targetId, r.reason, r.state, r.created_at AS at
      FROM (SELECT DISTINCT filter_id FROM filter_attestations WHERE contributor_id = ? AND kind = 'support' AND filter_id IS NOT NULL) f
      JOIN reports r ON r.target_type = 'filter' AND r.target_id = f.filter_id ORDER BY r.created_at DESC, r.report_id LIMIT ${RECENT}`)
      .bind(contributorId),
    db.prepare(`SELECT (SELECT count(*) FROM version_acceptances WHERE contributor_id = ?1) AS uploads,
      (SELECT count(DISTINCT v.work_id) FROM version_acceptances a JOIN versions v ON v.version_id = a.version_id
        WHERE a.contributor_id = ?1) AS items,
      (SELECT count(*) FROM filter_votes WHERE contributor_id = ?1 AND excluded = 0) AS votesCast,
      (SELECT count(*) FROM filter_votes WHERE contributor_id = ?1 AND excluded = 0 AND vote = 'up') AS votesCastUp,
      (SELECT count(*) FROM reports WHERE contributor_id = ?1) AS reportsFiled,
      (SELECT count(DISTINCT a.filter_id) FROM filter_attestations a JOIN master_filters m ON m.filter_id = a.filter_id AND m.served = 1
        JOIN filter_registry r ON r.filter_id = a.filter_id AND r.quarantined = 0
        WHERE a.contributor_id = ?1 AND a.counted = 1 AND a.kind = 'support') AS accepted`).bind(contributorId),
    db.prepare(`SELECT (SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'contributor:' || ?) AS bytes,
      (SELECT contributor_bytes FROM artifact_storage_policy WHERE policy_id = 1) AS quotaBytes`).bind(contributorId)]);
  if (!identity.results.length) throw new InsightNotFound();
  const person = identity.results[0];
  const labels = await resolveLabels(db, { versions: uploads.results.map(row => row.versionId),
    filters: [...votes.results.map(row => row.filterId), ...filed.results.filter(row => row.targetType === 'filter').map(row => row.targetId),
      ...against.results.map(row => row.targetId)],
    contributors: against.results.map(row => row.reporterId) });
  const [requests, writes] = await Promise.all([usageStatus(db, contributorId, 'worker_requests', now * 1000),
    usageStatus(db, contributorId, 'd1_writes', now * 1000)]);
  const total = totals.results[0];
  return {
    version: 1, observedAt: now * 1000,
    identity: { contributorId, displayName: person.displayName, state: person.state, quarantined: person.quarantined === 1,
      joinedAt: ms(person.joinedAt), profileUpdatedAt: person.profileUpdatedAt,
      membership: membership.results.map(row => ({ ...row, checkedAt: ms(row.checkedAt) })),
      moderation: { resource: `contributor:${contributorId}`, revision: person.moderationRevision } },
    totals: { uploads: total.uploads, items: total.items, accepted: total.accepted, votesCast: total.votesCast,
      votesUpShare: total.votesCast ? total.votesCastUp / total.votesCast : null, reportsFiled: total.reportsFiled },
    installations: installations.results.map(row => {
      const key = row.newestKey ? JSON.parse(row.newestKey) : null;
      return { installationId: row.installationId, createdAt: ms(row.createdAt), lastSyncAt: ms(row.lastSyncAt),
        keys: { active: row.activeKeys, retired: row.retiredKeys, revoked: row.revokedKeys },
        newestKey: key && { keyId: key.keyId, state: key.state, createdAt: ms(key.createdAt), retiredAt: ms(key.retiredAt) } };
    }),
    uploads: uploads.results.map(row => ({ submissionId: row.submissionId, at: ms(row.at), tier: row.tier,
      quarantined: row.quarantined === 1, state: row.state, entries: row.entries, ...labels.target('version', row.versionId) })),
    votes: votes.results.map(row => ({ eventId: row.eventId, kind: row.kind, vote: row.vote, at: ms(row.at),
      ...labels.target('filter', row.filterId) })),
    reportsFiled: filed.results.map(row => ({ reportId: row.reportId, reason: row.reason, state: row.state, at: ms(row.at),
      targetType: row.targetType, ...labels.target(row.targetType, row.targetId) })),
    reportsAgainst: against.results.map(row => ({ reportId: row.reportId, reason: row.reason, state: row.state, at: ms(row.at),
      reporter: labels.contributor(row.reporterId), ...labels.target('filter', row.targetId) })),
    usage: { source: 'application_observed', providerVerified: false, period: requests.period,
      requests: requests.observations, writes: writes.observations,
      storage: { bytes: Number(storage.results[0]?.bytes ?? 0), quotaBytes: storage.results[0]?.quotaBytes ?? null } },
  };
}
