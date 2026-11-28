import type { SqlDatabase, SqlStatement } from '../runtime/contracts';

export type VoteEventKind = 'cast' | 'changed' | 'removed' | 'excluded' | 'restored';
type Vote = 'up' | 'down';

/**
 * The history event for a contributor's vote moving from `prior` to `next` (`none` removes it), or
 * null when nothing changed. `vote` is the new value, or the withdrawn one for `removed`.
 */
export function voteTransition(prior: Vote | undefined, next: Vote | 'none'): { kind: VoteEventKind; vote: Vote } | null {
  if (next === 'none') return prior ? { kind: 'removed', vote: prior } : null;
  if (!prior) return { kind: 'cast', vote: next };
  return prior === next ? null : { kind: 'changed', vote: next };
}

/** Append one `vote_events` row; batch it with the vote change. `now` is Unix seconds. */
export function voteEventStatement(db: SqlDatabase, event: { filterId: string; contributorId: string; vote: Vote;
  kind: VoteEventKind; receiptId: string | null; now: number }): SqlStatement {
  return db.prepare(`INSERT INTO vote_events (filter_id, contributor_id, vote, kind, receipt_id, created_at)
    VALUES (?, ?, ?, ?, ?, ?)`).bind(event.filterId, event.contributorId, event.vote, event.kind, event.receiptId, event.now);
}

/** Append the `excluded` or `restored` event for an existing vote, copied from `filter_votes`. */
export function moderatedVoteEventStatement(db: SqlDatabase, filterId: string, contributorId: string,
  kind: 'excluded' | 'restored', now: number): SqlStatement {
  return db.prepare(`INSERT INTO vote_events (filter_id, contributor_id, vote, kind, receipt_id, created_at)
    SELECT filter_id, contributor_id, vote, ?, receipt_id, ? FROM filter_votes WHERE filter_id = ? AND contributor_id = ?`)
    .bind(kind, now, filterId, contributorId);
}
