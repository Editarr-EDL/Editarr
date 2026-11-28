import type { SqlDatabase } from '../../runtime/contracts';

export interface MaintenanceRun {
  runId: string; kind: string; startedAt: number; durationMs: number | null; outcome: string; detail: Record<string, unknown>;
}
interface RunRow { runId: string; kind: string; startedAt: number; durationMs: number | null; outcome: string; detailJson: string }

const RUN_COLUMNS = `run_id AS runId, kind, started_at AS startedAt, duration_ms AS durationMs, outcome, detail_json AS detailJson`;

/** Shape a `maintenance_runs` row for the API; an unreadable detail becomes `{}`. */
function runRow(row: RunRow): MaintenanceRun {
  let detail: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(row.detailJson);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) detail = parsed as Record<string, unknown>;
  } catch { /* keep {} */ }
  return { runId: row.runId, kind: row.kind, startedAt: row.startedAt, durationMs: row.durationMs, outcome: row.outcome, detail };
}

/** The newest run of `kind`, or null. */
export async function latestRun(db: SqlDatabase, kind: string): Promise<MaintenanceRun | null> {
  const row = await db.prepare(`SELECT ${RUN_COLUMNS} FROM maintenance_runs WHERE kind = ? ORDER BY started_at DESC, run_id DESC LIMIT 1`)
    .bind(kind).first<RunRow>();
  return row ? runRow(row) : null;
}

/** One keyset page (newest first) of runs of `kind`; `after` is `[startedAt, runId]` from the previous page. */
export async function runPage(db: SqlDatabase, kind: string, after: [number, string] | null, now: number) {
  const rows = (await db.prepare(`SELECT ${RUN_COLUMNS} FROM maintenance_runs WHERE kind = ?1
      AND (?2 IS NULL OR started_at < ?2 OR (started_at = ?2 AND run_id < ?3)) ORDER BY started_at DESC, run_id DESC LIMIT 21`)
    .bind(kind, after?.[0] ?? null, after?.[1] ?? '').all<RunRow>()).results;
  const items = rows.slice(0, 20).map(runRow), last = items.at(-1);
  return { version: 1, observedAt: now, items,
    nextCursor: rows.length > 20 && last ? btoa(JSON.stringify([last.startedAt, last.runId])) : null };
}
