import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { initializeDatabase, openDatabase } from '../../src/runtime/schema.mjs';
import { drainRuntime } from '../../src/runtime/lifecycle.mjs';

const runner = new URL('../../src/backups/runner.ts', import.meta.url).pathname;
const verifier = new URL('../../src/runtime/backup-verify.mjs', import.meta.url);

/** The backup runner bundled into a fresh module, as the service bundle uses it. */
async function bundledRunner(directory) {
  const outfile = join(directory, 'runner.mjs');
  await build({ stdin: { contents: `export * from ${JSON.stringify(runner)};`, resolveDir: directory, loader: 'js' },
    outfile, bundle: true, platform: 'node', format: 'esm', target: 'node24', logLevel: 'silent' });
  return import(pathToFileURL(outfile).href);
}

test('shutdown refuses backups requested by admitted requests and closes SQLite only after runs finish', async () => {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'community-shutdown-')));
  try {
    const { startBackup, backupDrain, closeBackups } = await bundledRunner(directory);
    const path = join(directory, 'community.sqlite'), root = join(directory, 'backups');
    mkdirSync(root, { mode: 0o700 });
    initializeDatabase(path);
    const { database } = openDatabase(path);
    const env = { CATALOG_DB: database, COMMUNITY_BACKUP_ROOT: root, BACKUP_VERIFIER: verifier };
    let closedAt = null;
    const close = database.close.bind(database);
    database.close = () => { closedAt = performance.now(); close(); };

    // A run started before shutdown must finish before the database closes.
    const before = await startBackup(env, 'manual', 'operator', Date.now());
    // An admitted request that reaches POST /backups/run only after maintenance has drained.
    let maintenanceStopped = false, late;
    const maintenance = { stop: async () => { await backupDrain(); maintenanceStopped = true; } };
    const request = (async () => {
      while (!maintenanceStopped) await new Promise(resolve => setImmediate(resolve));
      late = await startBackup(env, 'manual', 'operator', Date.now()).then(id => ({ id }), error => ({ error }));
    })();
    await drainRuntime({ database, active: new Set([request]), maintenance,
      backups: { close: closeBackups, drain: backupDrain } });

    assert.ok(closedAt !== null);
    assert.equal(late.id, undefined, 'a backup started after shutdown began');
    assert.deepEqual({ code: late.error.code, status: late.error.status }, { code: 'backups_shutting_down', status: 503 });
    const check = new DatabaseSync(path, { readOnly: true });
    try {
      assert.deepEqual(check.prepare('SELECT run_id, status FROM backup_runs').all().map(row => ({ ...row })),
        [{ run_id: before, status: 'verified' }]);
    } finally { check.close(); }
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
