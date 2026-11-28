import { chmodSync, existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { afterEach, describe, expect, it } from 'vitest';
import { backupDrain, backupRunning, scheduledBackup, startBackup } from '../src/backups/runner';
import { verifySnapshotFile } from '../src/backups/engine';
import { stageBackup } from '../src/backups/staging';
import { readBackupRun } from '../src/backups/store';
import { backupFixture, writePairs } from './helpers/backups';

let fixture: ReturnType<typeof backupFixture>;
afterEach(async () => { await fixture?.close(); });

const sha256 = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');
const mode = (path: string) => statSync(path).mode & 0o777;

describe('online backup engine', () => {
  it('takes a transaction-consistent verified snapshot while the serving connection keeps writing', async () => {
    fixture = backupFixture();
    writePairs(fixture.database, 0, 4000, 400);
    let written = 4000, writing = true;
    const writer = (async () => {
      while (writing) { writePairs(fixture.database, written, 5); written += 5; await new Promise(resolve => setImmediate(resolve)); }
    })();
    const runId = await startBackup(fixture.env, 'manual', 'operator-subject', Date.now());
    expect(backupRunning()).toBe(true);
    await expect(startBackup(fixture.env, 'manual', 'operator-subject', Date.now())).rejects.toMatchObject({ code: 'backup_running', status: 409 });
    await backupDrain();
    writing = false;
    await writer;
    expect(written).toBeGreaterThan(4000);
    const run = await readBackupRun(fixture.database, runId);
    expect(run).toMatchObject({ status: 'verified', integrity: 'ok', trigger: 'manual', actor: 'operator-subject', error_code: null });
    const file = join(fixture.root, run!.file_name!);
    expect(run!.sha256).toBe(sha256(file));
    expect(run!.bytes).toBe(statSync(file).size);
    expect(mode(file)).toBe(0o600);
    expect(readdirSync(fixture.root).filter(name => name.startsWith('.'))).toEqual([]);
    const snapshot = new DatabaseSync(file, { readOnly: true });
    try {
      const unpaired = snapshot.prepare(`SELECT count(*) AS count FROM (SELECT day FROM usage_daily GROUP BY day HAVING count(*) != 2)`).get();
      expect(unpaired!.count).toBe(0);
      expect(Number(snapshot.prepare('SELECT count(*) AS count FROM usage_daily').get()!.count)).toBeGreaterThanOrEqual(8000);
      expect(snapshot.prepare('PRAGMA journal_mode').get()!.journal_mode).toBe('delete');
    } finally { snapshot.close(); }
    expect(existsSync(`${file}-wal`) || existsSync(`${file}-shm`)).toBe(false);
    const audit = fixture.database.connection.prepare("SELECT command, target_id FROM moderation_actions WHERE resource = 'backup'").all();
    expect(audit).toEqual([{ command: 'backup_run', target_id: runId }]);
  });

  it('creates destination directories 0700 inside the root and records an unwritable destination as a failure', async () => {
    fixture = backupFixture();
    fixture.database.connection.exec("UPDATE backup_config SET destination = 'nightly/db'");
    const first = await startBackup(fixture.env, 'scheduled', null, Date.now());
    await backupDrain();
    expect(await readBackupRun(fixture.database, first)).toMatchObject({ status: 'verified', trigger: 'scheduled', actor: null });
    expect(mode(join(fixture.root, 'nightly'))).toBe(0o700);
    expect(mode(join(fixture.root, 'nightly/db'))).toBe(0o700);
    chmodSync(join(fixture.root, 'nightly/db'), 0o500);
    try {
      const second = await startBackup(fixture.env, 'scheduled', null, Date.now());
      await backupDrain();
      expect(await readBackupRun(fixture.database, second)).toMatchObject({ status: 'failed', error_code: 'destination_unwritable' });
    } finally { chmodSync(join(fixture.root, 'nightly/db'), 0o700); }
    expect(readdirSync(join(fixture.root, 'nightly/db')).filter(name => name.startsWith('.'))).toEqual([]);
  });

  it('marks runs left running by a stopped process as interrupted', async () => {
    fixture = backupFixture();
    fixture.database.connection.prepare("INSERT INTO backup_runs (run_id, started_at, trigger, status) VALUES ('stale', ?, 'scheduled', 'running')")
      .run(Date.now() - 1000);
    await startBackup(fixture.env, 'scheduled', null, Date.now());
    await backupDrain();
    expect(await readBackupRun(fixture.database, 'stale')).toMatchObject({ status: 'failed', error_code: 'interrupted' });
  });

  it('refuses a corrupted or foreign snapshot during verification', async () => {
    fixture = backupFixture();
    const garbage = join(fixture.root, 'garbage.sqlite');
    writeFileSync(garbage, 'not a database');
    await expect(verifySnapshotFile(garbage, true)).rejects.toMatchObject({ code: 'verification_failed', status: 409 });
    const foreign = join(fixture.root, 'foreign.sqlite');
    const other = new DatabaseSync(foreign);
    other.exec('CREATE TABLE schema_lineage (lineage TEXT); INSERT INTO schema_lineage VALUES (\'other\')');
    other.close();
    await expect(verifySnapshotFile(foreign, false)).rejects.toMatchObject({ code: 'verification_failed' });
  });

  it('stages a verified copy, re-verifies it and returns offline restore commands', async () => {
    fixture = backupFixture();
    const runId = await startBackup(fixture.env, 'manual', 'operator-subject', Date.now());
    await backupDrain();
    const run = (await readBackupRun(fixture.database, runId))!;
    const staged = await stageBackup(fixture.env, fixture.database, runId, 'operator-subject', Date.now());
    expect(staged.stagedPath).toBe(join(fixture.root, 'staged', run.file_name!));
    expect(sha256(staged.stagedPath)).toBe(run.sha256);
    expect(mode(staged.stagedPath)).toBe(0o600);
    expect(mode(join(fixture.root, 'staged'))).toBe(0o700);
    expect(staged.commands).toEqual([
      'docker compose -f community-service/compose.yaml stop community',
      `COMMUNITY_RESTORE_VOLUME=${staged.restoreVolume} docker compose -f community-service/compose.yaml run --rm --no-deps restore --confirm --source ${staged.stagedPath} --sha256 ${run.sha256}`,
    ]);
    expect((await stageBackup(fixture.env, fixture.database, runId, 'operator-subject', Date.now())).stagedPath).toBe(staged.stagedPath);
    writeFileSync(staged.stagedPath, 'tampered');
    await expect(stageBackup(fixture.env, fixture.database, runId, 'operator-subject', Date.now()))
      .rejects.toMatchObject({ code: 'backup_stage_conflict', status: 409 });
  });

  it('starts one scheduled run per due slot and none without a backup root', async () => {
    fixture = backupFixture();
    const saved = Date.parse('2026-11-24T12:00:00Z'), slot = Date.parse('2026-11-25T03:30:00Z');
    fixture.database.connection.prepare("UPDATE backup_config SET enabled = 1, frequency = 'daily', at_utc = '03:30', updated_at = ?").run(saved);
    const count = () => Number(fixture.database.connection.prepare('SELECT count(*) AS count FROM backup_runs').get()!.count);
    await scheduledBackup(fixture.env, slot - 1);
    await scheduledBackup({ ...fixture.env, COMMUNITY_BACKUP_ROOT: undefined }, slot);
    expect(count()).toBe(0);
    await scheduledBackup(fixture.env, slot + 1000);
    await scheduledBackup(fixture.env, slot + 3_600_000);
    expect(fixture.database.connection.prepare('SELECT trigger, status, started_at FROM backup_runs').all())
      .toEqual([{ trigger: 'scheduled', status: 'verified', started_at: slot + 1000 }]);
    await scheduledBackup(fixture.env, slot + 86_400_000);
    expect(count()).toBe(2);
  });
});
