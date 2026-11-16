import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
import { restoreAndValidate } from './validate.mjs';
import { reconcileCounters } from './counters.mjs';
import { protectedInput, durableFile } from './files.mjs';
import { CliError } from '../operator/transport.mjs';

try {
  const [directory, maximum] = process.argv.slice(2);
  const policy = JSON.parse(await protectedInput(join(directory, 'validation.json')));
  const sql = await protectedInput(join(directory, 'database.sql'), Number(maximum));
  const path = join(directory, 'restore.partial.sqlite');
  const original = await restoreAndValidate(sql, path, policy.bounds, policy.inventory);
  for (const key of ['schemaRevision', 'artifacts', 'payloadBytes'])
    if (original[key] !== policy.validation[key]) throw new CliError('restore_manifest_validation_mismatch');
  const db = new DatabaseSync(path, { allowExtension: false });
  try {
    const counters = await reconcileCounters(db, policy.bounds, original);
    const usage = original.inventory.filter(row => /usage|quota|budget|request_counts|nonces/.test(row.table)).map(row => {
      const columns = db.prepare(`PRAGMA table_info("${row.table}")`).all().map(column => column.name);
      const period = columns.includes('period') ? db.prepare(`SELECT min(period) AS first,max(period) AS last FROM "${row.table}"`).get() : null;
      return { ...row, period, source: 'original_snapshot_rows', providerFreshness: 'unknown' };
    });
    await durableFile(directory, 'restore-result.json', handle => handle.writeFile(JSON.stringify({ original, counters, usage })));
  } finally { db.close(); }
} catch (error) {
  process.stderr.write(error instanceof CliError ? error.code : 'restore_import_failed');
  process.exitCode = 1;
}