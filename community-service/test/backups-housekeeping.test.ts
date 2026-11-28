import { existsSync, mkdirSync, readdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { HISTORY_MS, HISTORY_ROWS, pruneRunHistory, sweepPartials } from '../src/backups/housekeeping';
import { backupDrain, backupHousekeeping, startBackup } from '../src/backups/runner';
import { stageBackup } from '../src/backups/staging';
import { readBackupRun } from '../src/backups/store';
import { backupFixture } from './helpers/backups';

let fixture: ReturnType<typeof backupFixture>;
afterEach(async () => { await fixture?.close(); });
const partial = (id: string) => `.community-20261125T033000Z-${id}.sqlite.partial`;

describe('backup housekeeping', () => {
  it('recovers interrupted runs and removes only their confined partial files', async () => {
    fixture = backupFixture();
    const { root } = fixture;
    fixture.database.connection.exec("UPDATE backup_config SET destination = 'nightly'");
    fixture.database.connection.prepare("INSERT INTO backup_runs (run_id, started_at, trigger, status) VALUES ('stale', ?, 'scheduled', 'running')")
      .run(Date.now() - 1000);
    for (const directory of ['nightly', 'staged', 'other', 'outside']) mkdirSync(join(fixture.directory, directory === 'outside' ? '' : 'backups', directory), { recursive: true });
    const kept = [join(root, 'other', partial('aaaaaaaa')), join(root, '.unrelated.partial'), join(root, 'community-20261125T033000Z-bbbbbbbb.sqlite'),
      join(fixture.directory, 'outside', partial('cccccccc'))];
    const removed = [join(root, partial('11111111')), join(root, 'nightly', partial('22222222')), join(root, 'staged', partial('33333333'))];
    for (const path of [...kept, ...removed]) writeFileSync(path, 'x');
    symlinkSync(join(fixture.directory, 'outside', partial('cccccccc')), join(root, partial('dddddddd')));
    await backupHousekeeping(fixture.env, Date.now());
    expect(await readBackupRun(fixture.database, 'stale')).toMatchObject({ status: 'failed', error_code: 'interrupted' });
    for (const path of removed) expect(existsSync(path), path).toBe(false);
    for (const path of kept) expect(existsSync(path), path).toBe(true);
    expect(readdirSync(root)).toContain(partial('dddddddd'));
  });

  it('leaves staging partials alone when a stage is in progress', async () => {
    fixture = backupFixture();
    mkdirSync(join(fixture.root, 'staged'));
    writeFileSync(join(fixture.root, 'staged', partial('33333333')), 'x');
    writeFileSync(join(fixture.root, partial('11111111')), 'x');
    expect(await sweepPartials(fixture.root, '', false)).toEqual([partial('11111111')]);
    expect(existsSync(join(fixture.root, 'staged', partial('33333333')))).toBe(true);
  });

  it('does not touch an active run', async () => {
    fixture = backupFixture();
    const runId = await startBackup(fixture.env, 'manual', 'operator-subject', Date.now());
    await backupHousekeeping(fixture.env, Date.now());
    await backupDrain();
    expect(await readBackupRun(fixture.database, runId)).toMatchObject({ status: 'verified' });
  });

  it('keeps only the newest staged copy and runs one stage at a time', async () => {
    fixture = backupFixture();
    const runs: string[] = [];
    for (let index = 0; index < 2; index++) {
      runs.push(await startBackup(fixture.env, 'manual', 'operator-subject', Date.now() + index * 1000));
      await backupDrain();
    }
    const first = await stageBackup(fixture.env, fixture.database, runs[0], 'operator-subject', Date.now());
    const [second, concurrent] = await Promise.allSettled([
      stageBackup(fixture.env, fixture.database, runs[1], 'operator-subject', Date.now()),
      stageBackup(fixture.env, fixture.database, runs[0], 'operator-subject', Date.now())]);
    expect(concurrent).toMatchObject({ status: 'rejected', reason: { code: 'backup_stage_running', status: 409 } });
    expect(second.status).toBe('fulfilled');
    const staged = (second as PromiseFulfilledResult<{ stagedPath: string }>).value.stagedPath;
    expect(readdirSync(join(fixture.root, 'staged'))).toEqual([staged.split('/').at(-1)]);
    expect(existsSync(first.stagedPath)).toBe(false);
  });

  it('bounds failed and pruned history but never deletes verified or running rows', async () => {
    fixture = backupFixture();
    const now = Date.parse('2026-11-25T00:00:00Z'), old = now - HISTORY_MS - 1;
    const insert = fixture.database.connection.prepare(
      "INSERT INTO backup_runs (run_id, started_at, trigger, status, file_name) VALUES (?, ?, 'scheduled', ?, ?)");
    insert.run('old-failed', old, 'failed', null);
    insert.run('old-pruned', old, 'pruned', 'gone.sqlite');
    insert.run('old-verified', old, 'verified', 'kept.sqlite');
    insert.run('running', old, 'running', null);
    for (let index = 0; index < HISTORY_ROWS + 5; index++)
      insert.run(`failed-${String(index).padStart(4, '0')}`, now - 1000 + index, 'failed', null);
    await pruneRunHistory(fixture.database, now);
    const ids = fixture.database.connection.prepare('SELECT run_id FROM backup_runs ORDER BY run_id').all().map(row => row.run_id);
    expect(ids).toContain('old-verified');
    expect(ids).toContain('running');
    expect(ids).not.toContain('old-failed');
    expect(ids).not.toContain('old-pruned');
    const failed = ids.filter(id => String(id).startsWith('failed-'));
    expect(failed).toHaveLength(HISTORY_ROWS);
    expect(failed[0]).toBe('failed-0005');
  });
});
