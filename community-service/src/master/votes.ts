import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { MergePolicy } from '../filters';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { admitRate, RouteError } from '../http/contributor-route';
import { completionStatements, replayOperation } from '../operations/completed';
import { availableVersion } from './download';
import { recomputeVersion } from './recompute';

export const voteInput = z.strictObject({ version: z.literal(1), vote: z.enum(['up', 'down', 'none']),
  receiptId: z.string().regex(/^[0-9a-f]{64}$/) });
export interface VoteResult { version: 1; operationId: string; filterId: string; versionId: string; vote: 'up' | 'down' | 'none'; revision: number }
export interface VoteOptions { service: string; policy: MergePolicy; now: number; votesPerHour: number }

const ineligible = (reason: 'self_vote' | 'receipt_required') => new RouteError(403, 'vote_ineligible', { reason });

/**
 * Check §7.4 eligibility: the receipt is the caller's and its snapshot contained the filter (or a
 * filter that absorbed it), and the caller has no weight-1 support attestation on the filter.
 */
async function eligibleFilter(db: SqlDatabase, identity: AuthenticatedContributor, filterId: string, receiptId: string) {
  // Follow the whole `mergedInto` chain of a retired ID to the filter it now belongs to.
  const filter = await db.prepare(`WITH RECURSIVE chain(id, depth) AS (SELECT ?1, 0 UNION ALL
      SELECT r.merged_into, c.depth + 1 FROM chain c JOIN filter_registry r ON r.filter_id = c.id
      WHERE r.state = 'retired' AND r.merged_into IS NOT NULL AND c.depth < 64)
    SELECT r.version_id, (SELECT id FROM chain ORDER BY depth DESC LIMIT 1) AS current
    FROM filter_registry r WHERE r.filter_id = ?1`).bind(filterId).first<{ version_id: string; current: string }>();
  if (!filter) throw new RouteError(404, 'filter_not_found');
  const receipt = await db.prepare(`SELECT 1 AS held FROM download_receipts d JOIN master_revision_filters f
    ON f.version_id = d.version_id AND f.revision = d.revision WHERE d.receipt_id = ? AND d.contributor_id = ? AND f.filter_id = ?`)
    .bind(receiptId, identity.contributorId, filterId).first();
  if (!receipt) throw ineligible('receipt_required');
  const own = await db.prepare(`SELECT 1 AS own FROM filter_attestations a JOIN submission_entries e USING (submission_id, entry_id)
    WHERE a.filter_id = ? AND a.contributor_id = ? AND a.kind = 'support' AND a.implicit = 0 AND e.weight = 1`)
    .bind(filter.current, identity.contributorId).first();
  if (own) throw ineligible('self_vote');
}

/** The version that owns a filter now; a concurrent merge moves it, so it is re-read on every attempt. */
async function filterVersion(db: SqlDatabase, filterId: string) {
  const row = await db.prepare('SELECT version_id FROM filter_registry WHERE filter_id = ?').bind(filterId).first<{ version_id: string }>();
  if (!row) throw new RouteError(404, 'filter_not_found');
  return row.version_id;
}

/** Set or remove (`none`) the caller's receipt-bound vote on a filter and recompute its version (§7.4). */
export async function castVote(db: SqlDatabase, identity: AuthenticatedContributor, filterId: string, value: unknown,
  options: VoteOptions): Promise<VoteResult> {
  const parsed = voteInput.safeParse(value);
  if (!parsed.success || !identity.operationId?.startsWith('vote-')) throw new RouteError(400, 'invalid_vote');
  const input = parsed.data, digest = JSON.stringify([filterId, identity.contentDigest]);
  const replayed = await replayOperation<VoteResult>(db, identity, 'vote', digest);
  if (replayed) return replayed;
  await admitRate(db, `vote:${identity.contributorId}`, options.votesPerHour, 3600, options.now);
  await eligibleFilter(db, identity, filterId, input.receiptId);
  let result: VoteResult | undefined;
  await recomputeVersion(db, () => filterVersion(db, filterId), options, {
    change: state => {
      // A moderator exclusion stays with the vote; removing an excluded vote keeps its excluded row.
      const mine = (vote: { filterId: string; contributorId: string }) => vote.filterId === filterId && vote.contributorId === identity.contributorId;
      const existing = state.votes.find(mine);
      state.votes = state.votes.filter(vote => !mine(vote));
      if (input.vote !== 'none') state.votes.push({ filterId, contributorId: identity.contributorId, vote: input.vote,
        excluded: existing?.excluded ?? false });
      else if (existing?.excluded) state.votes.push(existing);
    },
    extra: plan => {
      result = { version: 1, operationId: identity.operationId!, filterId, versionId: plan.versionId, vote: input.vote, revision: plan.revision };
      return [...completionStatements(db, identity, 'vote', digest, result, options.now),
        input.vote === 'none'
          ? db.prepare('DELETE FROM filter_votes WHERE filter_id = ? AND contributor_id = ? AND excluded = 0').bind(filterId, identity.contributorId)
          : db.prepare(`INSERT INTO filter_votes (filter_id, contributor_id, vote, receipt_id, excluded, updated_at) VALUES (?, ?, ?, ?, 0, ?)
              ON CONFLICT(filter_id, contributor_id) DO UPDATE SET vote = excluded.vote, receipt_id = excluded.receipt_id,
                updated_at = excluded.updated_at`).bind(filterId, identity.contributorId, input.vote, input.receiptId, options.now)];
    },
  }).catch(async error => {
    const raced = await replayOperation<VoteResult>(db, identity, 'vote', digest);
    if (raced) { result = raced; return; }
    throw error;
  });
  return result!;
}

/** `GET /v1/versions/{versionId}/votes`: the caller's own votes on the version's filters, at most 4,000. */
export async function ownVotes(db: SqlDatabase, identity: AuthenticatedContributor, versionId: string) {
  await availableVersion(db, versionId);
  const rows = await db.prepare(`SELECT v.filter_id AS filterId, v.vote FROM filter_votes v JOIN filter_registry r USING (filter_id)
    WHERE r.version_id = ? AND v.contributor_id = ? ORDER BY v.filter_id LIMIT 4000`).bind(versionId, identity.contributorId)
    .all<{ filterId: string; vote: 'up' | 'down' }>();
  return { version: 1, versionId, votes: rows.results };
}
