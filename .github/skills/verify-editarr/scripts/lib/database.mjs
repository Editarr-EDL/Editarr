import path from "node:path";
import { DatabaseSync } from "node:sqlite";

// The app commits in rollback-journal mode, so a reader waits out the writer's lock;
// one commit took about 1 s under host load in wave 16.
const BUSY_TIMEOUT_MS = 10_000;

/**
 * Run one read-only statement against the run's `scanner.db` and return its rows as
 * plain objects. The connection waits for a concurrent writer and is always closed.
 */
export function query(run, sql, ...values) {
  const db = new DatabaseSync(path.join(run.data, "scanner.db"), { readOnly: true, timeout: BUSY_TIMEOUT_MS });
  try { return db.prepare(sql).all(...values).map(row => ({ ...row })); }
  finally { db.close(); }
}
