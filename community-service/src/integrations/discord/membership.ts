import type { SqlDatabase, SqlStatement } from '../../runtime/contracts';
import { MEMBERSHIP_FRESHNESS_SECONDS, transientStatement } from '../../retention/transient';
import type { Env } from '../../config';
import type { MembershipCheck } from '../../security/contributor-auth';
import { fetchBotMember } from './member-provider';

const freshness = MEMBERSHIP_FRESHNESS_SECONDS;
const leaseSeconds = 10;

/** Persist freshness and shared provider coordination; secret changes invalidate cache authority. */
export function createBotMembership(env: Env, client: typeof fetch = fetch,
  clock?: () => number, enrollmentSubject?: string): MembershipCheck {
  return async (contributorId, now) => {
    const started = performance.now();
    const currentTime = () => clock ? clock() : now + Math.floor((performance.now() - started) / 1000);
    const db = env.CATALOG_DB;
    const guild = env.DISCORD_GUILD_ID;
    const token = env.DISCORD_BOT_TOKEN;
    if (!db || !guild || !token || !/^\d{17,20}$/.test(guild) ||
        !/^[\x21-\x7e]{1,2048}$/.test(token) || !Number.isSafeInteger(now) || now < 0) return 'unavailable';
    try {
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256',
        new TextEncoder().encode(`editarr-membership-v1:${guild}:${token}`)));
      const authority = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
      if (enrollmentSubject && !/^\d{17,20}$/.test(enrollmentSubject)) return 'unavailable';
      const linked = enrollmentSubject ? await db.prepare(
        'SELECT contributor_id, discord_user_id, state FROM contributors WHERE discord_user_id = ?')
        .bind(enrollmentSubject).first<{ contributor_id: string; discord_user_id: string; state: string }>() : null;
      if (linked) contributorId = linked.contributor_id;
      const person = enrollmentSubject ? linked ?? { discord_user_id: enrollmentSubject, state: 'active' } :
        await db.prepare('SELECT discord_user_id, state FROM contributors WHERE contributor_id = ?')
          .bind(contributorId).first<{ discord_user_id: string; state: string }>();
      if (!person || person.state !== 'active') return 'ineligible';
      async function cached() {
        if (enrollmentSubject && !linked) return null;
        return db!.prepare(`SELECT state, checked_at FROM discord_membership
          WHERE contributor_id = ? AND authority = ?`).bind(contributorId, authority)
          .first<{ state: 'eligible' | 'ineligible'; checked_at: number }>();
      }
      const previous = await cached();
      const readAt = currentTime();
      if (!Number.isSafeInteger(readAt) || readAt < now) return 'unavailable';
      if (previous && previous.checked_at <= readAt && readAt - previous.checked_at < freshness) return previous.state;
      await db.prepare('INSERT INTO discord_membership_refresh (authority) VALUES (?) ON CONFLICT DO NOTHING')
        .bind(authority).run();
      const lease = crypto.randomUUID();
      const claim = await db.prepare(`UPDATE discord_membership_refresh SET lease_id = ?, lease_until = ?
        WHERE authority = ? AND lease_until <= ? AND retry_at <= ?`).bind(
        lease, readAt + leaseSeconds, authority, readAt, readAt).run();
      if (claim.meta.changes !== 1) return 'unavailable';
      const raced = await cached();
      const refreshedAt = currentTime();
      if (!Number.isSafeInteger(refreshedAt) || refreshedAt < readAt ||
        refreshedAt >= readAt + leaseSeconds) return 'unavailable';
      if (raced && raced.checked_at <= refreshedAt && refreshedAt - raced.checked_at < freshness) {
        await db.prepare(`UPDATE discord_membership_refresh SET lease_id = NULL, lease_until = 0
          WHERE authority = ? AND lease_id = ?`).bind(authority, lease).run();
        return raced.state;
      }
      const result = await fetchBotMember(guild, person.discord_user_id, token, client);
      const completed = currentTime();
      if (!Number.isSafeInteger(completed) || completed < refreshedAt) return 'unavailable';
      const statements: SqlStatement[] = [];
      if (result.state !== 'unavailable' && (!enrollmentSubject || linked)) {
        statements.push(db.prepare(`INSERT INTO discord_membership (contributor_id, authority, state, checked_at)
          SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM discord_membership_refresh
            WHERE authority = ? AND lease_id = ? AND lease_until > ?)
          ON CONFLICT(contributor_id, authority) DO UPDATE SET state = excluded.state,
            checked_at = excluded.checked_at WHERE checked_at <= excluded.checked_at`).bind(
          contributorId, authority, result.state, refreshedAt, authority, lease, completed));
      }
      statements.push(db.prepare(`UPDATE discord_membership_refresh
        SET lease_id = NULL, lease_until = 0, retry_at = MAX(retry_at, ?)
        WHERE authority = ? AND lease_id = ? AND lease_until > ?`).bind(
        completed + result.retryAfter, authority, lease, completed));
      const updates = await db.batch(statements);
      if (updates[updates.length - 1].meta.changes !== 1) return 'unavailable';
      const current = await db.prepare(enrollmentSubject ?
        'SELECT state FROM contributors WHERE discord_user_id = ?' :
        'SELECT state FROM contributors WHERE contributor_id = ?')
        .bind(enrollmentSubject ?? contributorId).first<{ state: string }>();
      if (current?.state !== 'active' && (!enrollmentSubject || current)) return 'ineligible';
      return result.state;
    } catch { return 'unavailable'; }
  };
}

/** Remove expired evidence and idle coordination without erasing an active lease or cooldown. */
export async function pruneBotMembership(db: SqlDatabase, now: number, limit: number): Promise<void> {
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) {
    throw new Error('invalid_limit');
  }
  await db.batch([
    transientStatement(db, 'membershipEvidence', now * 1000, limit, 'prune'),
    transientStatement(db, 'membershipCoordination', now * 1000, limit, 'prune'),
  ]);
}