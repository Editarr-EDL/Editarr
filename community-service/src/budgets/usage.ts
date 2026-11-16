import type { SqlDatabase } from '../runtime/contracts';

export type UsageResource = 'worker_requests' | 'd1_writes';
export type UsageOutcome = 'allowed' | 'denied' | 'retried';

function periodFor(now: number): string {
  if (!Number.isSafeInteger(now) || now < 0) throw new Error('usage_unavailable');
  const date = new Date(now);
  if (Number.isNaN(date.getTime())) throw new Error('usage_unavailable');
  return date.toISOString().slice(0, 10);
}

export async function recordUsage(db: SqlDatabase, input: {
  contributorId: string; resource: UsageResource; outcome: UsageOutcome; units: number; now: number;
}): Promise<void> {
  const { contributorId, resource, outcome, units, now } = input;
  if (!contributorId || !['worker_requests', 'd1_writes'].includes(resource) ||
      !['allowed', 'denied', 'retried'].includes(outcome) ||
      !Number.isSafeInteger(units) || units < 0) throw new Error('usage_unavailable');
  await db.prepare(`INSERT INTO usage_observations
    (contributor_id, resource, period, outcome, occurrences, units, updated_at)
    VALUES (?, ?, ?, ?, 1, ?, ?)
    ON CONFLICT(contributor_id, resource, period, outcome) DO UPDATE SET
      occurrences = occurrences + 1, units = units + excluded.units,
      updated_at = MAX(updated_at, excluded.updated_at)`).bind(
    contributorId, resource, periodFor(now), outcome, units, now,
  ).run();
}

export async function usageStatus(db: SqlDatabase, contributorId: string,
  resource: UsageResource, now: number) {
  if (!contributorId || !['worker_requests', 'd1_writes'].includes(resource)) throw new Error('usage_unavailable');
  const period = periodFor(now);
  const rows = await db.prepare(`SELECT outcome, occurrences, units, updated_at
    FROM usage_observations WHERE contributor_id = ? AND resource = ? AND period = ?`)
    .bind(contributorId, resource, period)
    .all<{ outcome: UsageOutcome; occurrences: number; units: number; updated_at: number }>();
  const observations: Partial<Record<UsageOutcome, {
    occurrences: number; units: number; observedAt: number;
  }>> = {};
  for (const row of rows.results) observations[row.outcome] = {
    occurrences: row.occurrences, units: row.units, observedAt: row.updated_at,
  };
  return { source: 'application_observed', providerVerified: false,
    restoreProvenance: 'unknown', period, observations };
}