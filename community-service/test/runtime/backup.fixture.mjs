import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { journey, expectStatus } from './journey.fixture.mjs';
import { openDatabase } from '../../src/runtime/schema.mjs';
import { restoreDatabase } from '../../src/runtime/restore.mjs';

/** Configure, run, download, stage and offline-restore an online backup over real HTTP. */
export async function exercise(database, runtime) {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'community-backup-journey-')));
  try {
    await journey(database, async ({ admin, env }) => {
      env.COMMUNITY_BACKUP_ROOT = directory;
      // This fixture is bundled elsewhere; point the runtime-relative modules back at the sources.
      const runtimeDirectory = runtime.applicationRoot
        ? new URL('community-service/src/runtime/', pathToFileURL(join(runtime.applicationRoot, '/'))) : null;
      if (runtimeDirectory) env.BACKUP_VERIFIER = new URL('backup-verify.mjs', runtimeDirectory);
      const migrations = runtimeDirectory ? new URL('../../migrations/', runtimeDirectory) : new URL('file:///app/migrations/');
      const initial = await admin('/admin/v1/backups'); expectStatus(initial);
      assert.equal(initial.json.destination.root, directory);
      assert.equal(initial.json.next, null);
      expectStatus(await admin('/admin/v1/backups/run', {}, { Origin: 'https://hostile.example' }), 403);
      const started = await admin('/admin/v1/backups/run', {}); expectStatus(started, 202);
      let view;
      for (let attempt = 0; attempt < 200; attempt++) {
        view = (await admin('/admin/v1/backups')).json;
        if (!view.running) break;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      const run = view.runs.find(item => item.runId === started.json.runId);
      assert.equal(run.status, 'verified', JSON.stringify(run));
      assert.equal(statSync(join(directory, run.fileName)).mode & 0o777, 0o600);
      const download = await admin(`/admin/v1/backups/${run.runId}/download`); expectStatus(download);
      assert.match(download.headers['content-disposition'], /^attachment; filename="community-/);
      assert.equal(download.headers['cache-control'], 'private, no-store');
      assert.equal(Number(download.headers['content-length']), run.bytes);
      const staged = await admin(`/admin/v1/backups/${run.runId}/stage`, {}); expectStatus(staged);
      assert.equal(staged.json.sha256, run.sha256);
      const target = join(directory, 'restored.sqlite');
      const restored = await restoreDatabase(staged.json.stagedPath, target, staged.json.sha256, migrations);
      assert.equal(restored.version, restored.current);
      const reopened = openDatabase(target, migrations);
      try { assert.equal(reopened.database.connection.prepare('SELECT count(*) AS n FROM backup_runs').get().n, 1); }
      finally { reopened.database.close(); }
    }, runtime);
  } finally { rmSync(directory, { recursive: true, force: true }); }
}
