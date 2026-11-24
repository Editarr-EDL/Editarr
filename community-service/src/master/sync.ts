import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { MasterPolicy } from '../filters/policy';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { RouteError } from '../http/contributor-route';

const token = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
export const syncInput = z.strictObject({ version: z.literal(1), runId: token,
  items: z.array(z.strictObject({ versionId: token, revision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER) })).min(1).max(500),
}).refine(input => new Set(input.items.map(item => item.versionId)).size === input.items.length);
type SyncPolicy = Pick<MasterPolicy, 'syncRunsPerDay' | 'syncMaxBatchesPerRun' | 'syncRunWindowSeconds'>;
export type SyncChange = { versionId: string; status: 'changed'; revision: number }
  | { versionId: string; status: 'merged'; revision: null; mergedInto: string }
  | { versionId: string; status: 'unavailable'; revision: null };

const DAY = 86_400;

/** Earliest time a new run is allowed for the installation, given its runs in the last 24 hours. */
async function nextRunAllowedAt(db: SqlDatabase, installationId: string, policy: SyncPolicy, now: number) {
  const runs = await db.prepare(`SELECT started_at FROM sync_runs WHERE installation_id = ? AND started_at > ?
    ORDER BY started_at DESC`).bind(installationId, now - DAY).all<{ started_at: number }>();
  return runs.results.length < policy.syncRunsPerDay ? now : runs.results[policy.syncRunsPerDay - 1].started_at + DAY;
}

/**
 * Admit one batch of a sync run (§7.5): a run is at most `syncMaxBatchesPerRun` requests within
 * `syncRunWindowSeconds` of its first request, and an installation may start `syncRunsPerDay` runs
 * in any rolling 24 hours. Refusals are `429 sync_rate_limited` with `Retry-After`.
 */
async function admitBatch(db: SqlDatabase, installationId: string, runId: string, policy: SyncPolicy, now: number) {
  const continued = await db.prepare(`UPDATE sync_runs SET batches = batches + 1 WHERE installation_id = ? AND run_id = ?
    AND batches < ? AND started_at + ? >= ? RETURNING batches`).bind(installationId, runId, policy.syncMaxBatchesPerRun,
    policy.syncRunWindowSeconds, now).first();
  if (continued) return;
  const existing = await db.prepare('SELECT 1 AS found FROM sync_runs WHERE installation_id = ? AND run_id = ?').bind(installationId, runId).first();
  const started = existing ? null : await db.prepare(`INSERT INTO sync_runs (installation_id, run_id, started_at, batches)
    SELECT ?, ?, ?, 1 WHERE (SELECT count(*) FROM sync_runs WHERE installation_id = ? AND started_at > ?) < ?
    ON CONFLICT DO NOTHING RETURNING batches`).bind(installationId, runId, now, installationId, now - DAY, policy.syncRunsPerDay).first();
  if (started) return;
  const retry = Math.max(1, await nextRunAllowedAt(db, installationId, policy, now) - now);
  throw new RouteError(429, 'sync_rate_limited', {}, { 'Retry-After': String(retry) });
}

/** Report versions whose revision advanced, were merged, or became unavailable; unchanged items are omitted. */
export async function syncRevisions(db: SqlDatabase, identity: AuthenticatedContributor, value: unknown, policy: SyncPolicy, now: number) {
  const parsed = syncInput.safeParse(value);
  if (!parsed.success) throw new RouteError(400, 'invalid_sync');
  const installation = await db.prepare('SELECT installation_id FROM installation_keys WHERE key_id = ?').bind(identity.keyId)
    .first<{ installation_id: string }>();
  if (!installation) throw new Error('key_inactive');
  await admitBatch(db, installation.installation_id, parsed.data.runId, policy, now);
  const rows = await db.prepare(`SELECT v.version_id, v.status, v.revision, v.quarantined, v.merged_into FROM versions v
    WHERE v.version_id IN (SELECT value ->> '$.versionId' FROM json_each(?))`).bind(JSON.stringify(parsed.data.items))
    .all<{ version_id: string; status: string; revision: number; quarantined: number; merged_into: string | null }>();
  const versions = new Map(rows.results.map(row => [row.version_id, row]));
  const changed: SyncChange[] = [];
  for (const item of parsed.data.items) {
    const version = versions.get(item.versionId);
    if (!version) changed.push({ versionId: item.versionId, status: 'unavailable', revision: null });
    else if (version.status === 'merged_into') changed.push({ versionId: item.versionId, status: 'merged', revision: null, mergedInto: version.merged_into! });
    else if (version.quarantined === 1) changed.push({ versionId: item.versionId, status: 'unavailable', revision: null });
    else if (version.revision > item.revision) changed.push({ versionId: item.versionId, status: 'changed', revision: version.revision });
  }
  return { version: 1, changed, nextRunAllowedAt: await nextRunAllowedAt(db, installation.installation_id, policy, now) };
}
