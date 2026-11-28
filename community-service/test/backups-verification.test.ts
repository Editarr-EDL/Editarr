import { readdirSync } from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import { BackupError } from '../src/backups/errors';
import { backupDrain, startBackup } from '../src/backups/runner';
import { readBackupRun } from '../src/backups/store';
import { backupFixture } from './helpers/backups';

vi.mock('../src/backups/engine', async original => ({
  ...await original<typeof import('../src/backups/engine')>(),
  verifySnapshotFile: vi.fn(async () => { throw new BackupError('integrity_failed', 409); }),
}));

let fixture: ReturnType<typeof backupFixture>;
afterEach(async () => { await fixture?.close(); });

it('records a failed verification, deletes the unverified copy and releases the lock', async () => {
  fixture = backupFixture();
  const runId = await startBackup(fixture.env, 'manual', 'operator-subject', Date.now());
  await backupDrain();
  expect(await readBackupRun(fixture.database, runId)).toMatchObject({ status: 'failed', integrity: 'failed',
    error_code: 'integrity_failed', file_name: null, sha256: null });
  expect(readdirSync(fixture.root)).toEqual([]);
  const next = await startBackup(fixture.env, 'manual', 'operator-subject', Date.now());
  await backupDrain();
  expect(await readBackupRun(fixture.database, next)).toMatchObject({ status: 'failed' });
});
