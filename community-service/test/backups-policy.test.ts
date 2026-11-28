import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, chmodSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { backupDue, latestBackupSlot, nextBackupAt, type ScheduleConfig } from '../src/backups/schedule';
import { backupsToPrune, pruneBackups } from '../src/backups/prune';
import { backupRoot, confinedDirectory, confinedFile, destinationState, normalizeDestination } from '../src/backups/destination';
import { backupFixture } from './helpers/backups';

const at = (iso: string) => Date.parse(iso);
const daily: ScheduleConfig = { enabled: 1, frequency: 'daily', at_utc: '03:30' };

describe('backup schedule', () => {
  it('computes the next slot for each frequency', () => {
    const now = at('2026-11-25T10:00:00Z');
    expect(nextBackupAt(daily, now)).toBe(at('2026-11-26T03:30:00Z'));
    expect(nextBackupAt({ ...daily, frequency: 'hourly' }, now)).toBe(at('2026-11-25T10:30:00Z'));
    expect(nextBackupAt({ ...daily, frequency: 'every_6h' }, now)).toBe(at('2026-11-25T15:30:00Z'));
    expect(nextBackupAt({ ...daily, frequency: 'weekly' }, now)).toBe(at('2026-11-29T03:30:00Z'));
    expect(new Date(nextBackupAt({ ...daily, frequency: 'weekly' }, now)!).getUTCDay()).toBe(0);
    expect(nextBackupAt(daily, at('2026-11-25T03:30:00Z'))).toBe(at('2026-11-26T03:30:00Z'));
    expect(nextBackupAt({ ...daily, enabled: 0 }, now)).toBeNull();
  });

  it('is due once per slot, only for slots after the configuration was saved', () => {
    const config = { ...daily, updated_at: at('2026-11-25T01:00:00Z') };
    expect(backupDue(config, null, at('2026-11-25T03:29:59Z'))).toBe(false);
    expect(backupDue(config, null, at('2026-11-25T03:30:00Z'))).toBe(true);
    expect(backupDue(config, at('2026-11-25T03:30:05Z'), at('2026-11-25T09:00:00Z'))).toBe(false);
    expect(backupDue(config, at('2026-11-25T03:30:05Z'), at('2026-11-26T03:31:00Z'))).toBe(true);
    expect(backupDue({ ...config, updated_at: at('2026-11-25T04:00:00Z') }, null, at('2026-11-25T12:00:00Z'))).toBe(false);
    expect(backupDue({ ...config, enabled: 0 }, null, at('2026-11-25T03:30:00Z'))).toBe(false);
    expect(latestBackupSlot(config, at('2026-11-25T03:29:00Z'))).toBe(at('2026-11-24T03:30:00Z'));
  });
});

describe('backup pruning', () => {
  const day = (n: number) => ({ run_id: `run-${String(n).padStart(3, '0')}`, started_at: at('2026-11-01T03:30:00Z') + n * 86_400_000 });

  it('keeps the newest keep_last plus the newest backup of each of the newest keep_weekly weeks', () => {
    const runs = Array.from({ length: 30 }, (_, n) => day(n));
    const pruned = new Set(backupsToPrune(runs, 3, 2));
    const kept = runs.filter(run => !pruned.has(run.run_id)).map(run => run.run_id);
    // Nov 28–30 are newest; Nov 30 is a Monday (new week), so the previous week's newest is Sun Nov 29.
    expect(kept).toEqual(['run-027', 'run-028', 'run-029']);
    const weekly = new Set(backupsToPrune(runs, 1, 3));
    expect(runs.filter(run => !weekly.has(run.run_id)).map(run => run.run_id)).toEqual(['run-021', 'run-028', 'run-029']);
  });

  it('never prunes the newest verified backup', () => {
    expect(backupsToPrune([day(1), day(5), day(3)], 1, 0)).toEqual(['run-003', 'run-001']);
    expect(backupsToPrune([day(1)], 1, 0)).toEqual([]);
  });

  it('deletes only recorded files and marks their runs pruned', async () => {
    const fixture = backupFixture();
    try {
      const insert = fixture.database.connection.prepare(`INSERT INTO backup_runs (run_id, started_at, trigger, file_name,
        status, sha256, bytes) VALUES (?, ?, 'scheduled', ?, 'verified', 'x', 1)`);
      for (const n of [1, 2, 3]) { writeFileSync(join(fixture.root, `b${n}.sqlite`), 'x'); insert.run(`r${n}`, n, `b${n}.sqlite`); }
      writeFileSync(join(fixture.root, 'offline.sqlite'), 'x');
      expect(await pruneBackups(fixture.database, fixture.root, 1, 0)).toEqual(['r2', 'r1']);
      const states = fixture.database.connection.prepare('SELECT run_id, status FROM backup_runs ORDER BY run_id').all();
      expect(states.map(row => row.status)).toEqual(['pruned', 'pruned', 'verified']);
      await expect(confinedFile(fixture.root, 'b1.sqlite')).rejects.toThrow();
      expect(await confinedFile(fixture.root, 'b3.sqlite')).toBe(join(fixture.root, 'b3.sqlite'));
      expect(await confinedFile(fixture.root, 'offline.sqlite')).toBe(join(fixture.root, 'offline.sqlite'));
    } finally { await fixture.close(); }
  });
});

describe('backup destination confinement', () => {
  const directory = realpathSync(mkdtempSync(join(tmpdir(), 'community-destination-')));
  const root = join(directory, 'root'), outside = join(directory, 'outside');
  mkdirSync(root); mkdirSync(outside);
  afterAll(() => { chmodSync(join(root, 'locked'), 0o700); rmSync(directory, { recursive: true, force: true }); });

  it('normalizes relative and absolute in-root destinations and refuses everything else', () => {
    expect(normalizeDestination('', root)).toBe('');
    expect(normalizeDestination('nightly/db/', root)).toBe('nightly/db');
    expect(normalizeDestination(`${root}/nightly`, root)).toBe('nightly');
    expect(normalizeDestination('/configured/root/x', root, '/configured/root')).toBe('x');
    for (const [value, code] of [[outside, 'backup_destination_outside_root'], [`${root}-sibling/x`, 'backup_destination_outside_root'],
      ['../outside', 'backup_destination_outside_root'], ['a/../../outside', 'backup_destination_outside_root'],
      ['staged', 'backup_destination_invalid'], ['.hidden', 'backup_destination_invalid'], ['a\\b', 'backup_destination_invalid'],
      ['a/b/c/d/e', 'backup_destination_invalid'], ['bad name', 'backup_destination_invalid']] as const)
      expect(() => normalizeDestination(value, root), value).toThrow(code);
  });

  it('refuses symlinked components even when they point inside the root', async () => {
    symlinkSync(outside, join(root, 'escape'));
    mkdirSync(join(root, 'inside'));
    symlinkSync(join(root, 'inside'), join(root, 'alias'));
    await expect(confinedDirectory(root, 'escape', true)).rejects.toMatchObject({ code: 'backup_destination_symlink' });
    await expect(confinedDirectory(root, 'escape/deeper', true)).rejects.toMatchObject({ code: 'backup_destination_symlink' });
    await expect(confinedDirectory(root, 'alias', true)).rejects.toMatchObject({ code: 'backup_destination_symlink' });
    writeFileSync(join(outside, 'secret.sqlite'), 'x');
    await expect(confinedFile(root, 'escape/secret.sqlite')).rejects.toMatchObject({ code: 'backup_destination_symlink' });
    symlinkSync(join(outside, 'secret.sqlite'), join(root, 'linked.sqlite'));
    await expect(confinedFile(root, 'linked.sqlite')).rejects.toMatchObject({ code: 'backup_destination_invalid' });
  });

  it('resolves the root through symlinks, and refuses relative or missing roots', async () => {
    symlinkSync(root, join(directory, 'root-link'));
    expect(await backupRoot(join(directory, 'root-link'))).toBe(root);
    expect(await backupRoot(undefined)).toBeNull();
    await expect(backupRoot('relative/root')).rejects.toMatchObject({ code: 'backup_root_invalid', status: 503 });
    await expect(backupRoot(join(directory, 'missing'))).rejects.toMatchObject({ code: 'backup_root_invalid' });
  });

  it('reports writability and free space, and an unwritable directory', async () => {
    const ok = await destinationState(root, 'probe', true);
    expect(ok).toMatchObject({ configured: true, writable: true, root, path: join(root, 'probe'), error: null });
    expect(ok.freeBytes).toBeGreaterThan(0);
    mkdirSync(join(root, 'locked'), { mode: 0o500 });
    expect(await destinationState(root, 'locked', true)).toMatchObject({ writable: false, error: 'destination_unwritable' });
    expect(await destinationState(root, 'locked', false)).toMatchObject({ writable: false, error: 'destination_unwritable' });
    expect(await destinationState(root, 'escape', false)).toMatchObject({ writable: false, error: 'backup_destination_symlink' });
    expect(await destinationState(undefined, '', false)).toMatchObject({ configured: false, root: null, error: 'backups_not_configured' });
  });
});
