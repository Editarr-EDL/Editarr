import type { SqlDatabase, SqlStatement } from '../runtime/contracts';

export const MEMBERSHIP_FRESHNESS_SECONDS = 900;
export const RETENTION_BATCH_SIZE = 1000;

interface Selection {
  table: string;
  key: string;
  sql: string;
  values: (now: number) => unknown[];
}

const selections = {
  oauthStates: { table: 'discord_oauth_states', key: 'state_id',
    sql: 'SELECT state_id FROM discord_oauth_states WHERE expires_at < ? ORDER BY expires_at LIMIT ?',
    values: (now: number) => [now] },
  recoveryAuthorizations: { table: 'operations', key: 'rowid',
    sql: `SELECT authorization.rowid FROM operations AS authorization WHERE kind = 'key_recovery_authorization'
      AND state = 'completed' AND updated_at < ? AND json_extract(authorization.result, '$.expiresAt') < ?
      AND NOT EXISTS (SELECT 1 FROM operations AS recovery WHERE recovery.contributor_id = authorization.contributor_id
        AND recovery.kind = 'key_recovery' AND recovery.operation_id = json_extract(authorization.result, '$.operationId')
        AND json_extract(recovery.result, '$.stateId') = authorization.operation_id)
      ORDER BY authorization.updated_at LIMIT ?`,
    values: (now: number) => [now - 600_000, now] },
  requestNonces: { table: 'request_nonces', key: 'rowid',
    sql: 'SELECT rowid FROM request_nonces WHERE expires_at < ? LIMIT ?',
    values: (now: number) => [Math.floor(now / 1000)] },
  enrollmentStates: { table: 'enrollment_states', key: 'state_id',
    sql: 'SELECT state_id FROM enrollment_states WHERE expires_at < ? LIMIT ?',
    values: (now: number) => [Math.floor(now / 1000) * 1000] },
  brokerObservations: { table: 'oauth_usage_counters', key: '(resource, period)',
    sql: 'SELECT resource, period FROM oauth_usage_counters WHERE period < ? ORDER BY period LIMIT ?',
    values: (now: number) => [new Date(now).toISOString().slice(0, 7)] },
  membershipEvidence: { table: 'discord_membership', key: '(contributor_id, authority)',
    sql: 'SELECT contributor_id, authority FROM discord_membership WHERE checked_at <= ? ORDER BY checked_at LIMIT ?',
    values: (now: number) => [Math.floor(now / 1000) - MEMBERSHIP_FRESHNESS_SECONDS] },
  membershipCoordination: { table: 'discord_membership_refresh', key: 'authority',
    sql: `SELECT authority FROM discord_membership_refresh WHERE lease_until <= ? AND retry_at <= ?
      AND NOT EXISTS (SELECT 1 FROM discord_membership WHERE
        discord_membership.authority = discord_membership_refresh.authority) LIMIT ?`,
    values: (now: number) => [Math.floor(now / 1000), Math.floor(now / 1000)] },
  profileNameEvents: { table: 'profile_name_changes', key: '(contributor_id, operation_id)',
    sql: 'SELECT contributor_id, operation_id FROM profile_name_changes WHERE changed_at <= ? ORDER BY changed_at LIMIT ?',
    values: (now: number) => [now - 3_600_000] },
} satisfies Record<string, Selection>;

export type TransientCategory = keyof typeof selections;
export const TRANSIENT_CATEGORIES = Object.freeze(Object.keys(selections) as TransientCategory[]);

export function transientStatement(db: SqlDatabase, category: TransientCategory,
  now: number, limit: number, mode: 'preview' | 'prune'): SqlStatement {
  if (!Number.isSafeInteger(now) || !Number.isSafeInteger(limit) || limit < 1 || limit > RETENTION_BATCH_SIZE) {
    throw new Error('invalid_limit');
  }
  const selection = selections[category];
  const sql = mode === 'preview' ? `SELECT COUNT(*) AS eligible FROM (${selection.sql})`
    : `DELETE FROM ${selection.table} WHERE ${selection.key} IN (${selection.sql})`;
  return db.prepare(sql).bind(...selection.values(now), mode === 'preview' ? limit + 1 : limit);
}