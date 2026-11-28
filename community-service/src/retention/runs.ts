import type { SqlDatabase } from '../runtime/contracts';

export type MaintenanceKind = 'retention' | 'recompute_all' | 'metrics';
export type MaintenanceOutcome = 'ok' | 'batch_limit' | 'skipped' | 'failed';

/**
 * Record one `maintenance_runs` row. A retried `runId` of the same kind (a recompute-all action retried
 * after a failure) replaces the earlier attempt's time, outcome and detail, so the latest result shows. Times are milliseconds;
 * `detail` must hold only counts and codes, never secrets or contributor data.
 */
export async function recordMaintenanceRun(db: SqlDatabase, run: { runId: string; kind: MaintenanceKind; startedAt: number;
  durationMs: number | null; outcome: MaintenanceOutcome; detail?: Record<string, unknown> }) {
  await db.prepare(`INSERT INTO maintenance_runs (run_id, kind, started_at, duration_ms, outcome, detail_json)
    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET
      started_at = excluded.started_at, duration_ms = excluded.duration_ms, outcome = excluded.outcome,
      detail_json = excluded.detail_json WHERE maintenance_runs.kind = excluded.kind`).bind(run.runId, run.kind, run.startedAt,
    run.durationMs === null ? null : Math.max(0, Math.round(run.durationMs)), run.outcome, JSON.stringify(run.detail ?? {})).run();
}
