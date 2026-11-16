import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { activePublicationKey } from './publication';

export const voteValue = z.enum(['like', 'dislike', 'neutral']);
export const voteInput = z.strictObject({ version: z.literal(1), vote: voteValue });
export const voteLimits = z.strictObject({ requestBytes: z.number().int().min(1).max(8192),
  maxQueries: z.number().int().min(1).max(64) });
export interface VoteResult {
  version: 1; operationId: string; revisionId: string; vote: z.infer<typeof voteValue>;
}
export interface OwnVote {
  version: 1; revisionId: string; vote: z.infer<typeof voteValue>; eligible: boolean;
}
export interface VoteAggregates {
  version: 1; revisionId: string; likes: number; dislikes: number;
}

async function revision(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string) {
  const row = await db.prepare(`SELECT r.contributor_id AS author, i.counted
    FROM contribution_revisions r JOIN contribution_publications p USING (revision_id)
    JOIN contribution_statistics s USING (revision_id)
    JOIN contributors author ON author.contributor_id = r.contributor_id
    LEFT JOIN import_receipts i ON i.revision_id = r.revision_id AND i.contributor_id = ?
    WHERE r.revision_id = ? AND p.state = 'published' AND author.state = 'active'`).bind(identity.contributorId, revisionId)
    .first<{ author: string; counted: number | null }>();
  if (!row) throw new Error('revision_not_available');
  return row;
}

async function operation(db: SqlDatabase, contributorId: string, operationId: string) {
  return db.prepare(`SELECT request_digest, result FROM operations
    WHERE contributor_id = ? AND kind = 'vote' AND operation_id = ? AND state = 'completed'`)
    .bind(contributorId, operationId).first<{ request_digest: string; result: string }>();
}

/** Commit a contributor-scoped vote, retry result and nonce in one SQLite transaction. */
export async function setVote(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string,
  inputValue: unknown | (() => unknown), now: number): Promise<VoteResult> {
  const input = voteInput.parse(typeof inputValue === 'function' ? inputValue() : inputValue);
  if (!identity.operationId) throw new Error('invalid_vote');
  const row = await revision(db, identity, revisionId);
  if (row.author === identity.contributorId || row.counted !== 1) throw new Error('vote_ineligible');
  if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
  const digest = JSON.stringify([revisionId, identity.contentDigest]);
  const old = await operation(db, identity.contributorId, identity.operationId);
  if (old) {
    if (old.request_digest !== digest) throw new Error('operation_conflict');
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    await revision(db, identity, revisionId);
    return JSON.parse(old.result) as VoteResult;
  }
  const result: VoteResult = { version: 1, operationId: identity.operationId, revisionId, vote: input.vote };
  try {
    await db.batch([
      db.prepare('INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id) VALUES (?, ?, ?, ?)')
        .bind(identity.keyId, identity.nonce, identity.expiresAt, identity.contributorId),
      db.prepare(`INSERT INTO operations (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (?, 'vote', ?, ?, 'completed', ?, ?, ?)`)
        .bind(identity.contributorId, identity.operationId, digest, JSON.stringify(result), now * 1000, now * 1000),
      db.prepare(`INSERT INTO revision_votes (contributor_id, revision_id, value, key_id, updated_at)
        VALUES (?, ?, ?, ?, ?) ON CONFLICT(contributor_id, revision_id) DO UPDATE SET
          value = excluded.value, key_id = excluded.key_id, updated_at = excluded.updated_at`)
        .bind(identity.contributorId, revisionId, input.vote, identity.keyId, now),
    ]);
  } catch (error) {
    const raced = await operation(db, identity.contributorId, identity.operationId);
    if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
    if (!raced) throw error;
    if (raced.request_digest !== digest) throw new Error('operation_conflict');
    return JSON.parse(raced.result) as VoteResult;
  }
  return result;
}

/** Read current private vote or public totals without exposing voter identities. */
export async function readVote(db: SqlDatabase, identity: AuthenticatedContributor, revisionId: string,
  own: boolean, now: number) {
  const row = await revision(db, identity, revisionId);
  const result = own ? { version: 1, revisionId,
    vote: (await db.prepare('SELECT value FROM revision_votes WHERE contributor_id = ? AND revision_id = ?')
      .bind(identity.contributorId, revisionId).first<{ value: z.infer<typeof voteValue> }>())?.value ?? 'neutral',
    eligible: row.author !== identity.contributorId && row.counted === 1,
  } : { version: 1, revisionId,
    ...(await db.prepare('SELECT likes, dislikes FROM contribution_statistics WHERE revision_id = ?')
      .bind(revisionId).first<{ likes: number; dislikes: number }>())!,
  };
  if (!await activePublicationKey(db, identity)) throw new Error('key_inactive');
  await revision(db, identity, revisionId);
  return result;
}

/** Rebuild bounded vote aggregates from source rows, preserving import counters. */
export async function reconcileVoteCounts(db: SqlDatabase, revisionIds: string[]) {
  if (revisionIds.length > 100 || revisionIds.some(id => !/^[A-Za-z0-9_.-]{1,128}$/.test(id)))
    throw new Error('invalid_reconciliation');
  if (!revisionIds.length) return [];
  return db.batch([...new Set(revisionIds)].map(id => db.prepare(`UPDATE contribution_statistics SET
    likes = (SELECT count(*) FROM revision_votes v WHERE revision_id = ? AND value = 'like'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1)),
    dislikes = (SELECT count(*) FROM revision_votes v WHERE revision_id = ? AND value = 'dislike'
      AND NOT EXISTS (SELECT 1 FROM vote_exclusions e WHERE e.contributor_id = v.contributor_id AND e.revision_id = v.revision_id AND e.excluded = 1))
    WHERE revision_id = ?`).bind(id, id, id)));
}