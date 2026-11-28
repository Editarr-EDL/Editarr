import assert from 'node:assert/strict';
import { test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { initializeDatabase } from '../../src/runtime/schema.mjs';

const manage = new URL('../../src/runtime/manage.mjs', import.meta.url).pathname;
const run = (database, ...args) => spawnSync(process.execPath, [manage, ...args],
  { encoding: 'utf8', env: { ...process.env, COMMUNITY_DATABASE: database }, timeout: 30000 });
const digest = path => createHash('sha256').update(readFileSync(path)).digest('hex');

test('restore copies a checksum-matched staged snapshot into a new database and refuses everything else', () => {
  const directory = mkdtempSync(join(tmpdir(), 'community-restore-'));
  try {
    const staged = join(directory, 'staged.sqlite');
    initializeDatabase(staged);
    const connection = new DatabaseSync(staged);
    connection.exec("UPDATE backup_config SET keep_last = 7");
    connection.close();
    const sha256 = digest(staged), target = join(directory, 'community.sqlite');

    assert.match(run(target, 'restore', '--confirm', '--source', staged).stderr, /^usage:/);
    assert.match(run(target, 'restore', '--confirm', '--source', staged, '--sha256', 'f'.repeat(64)).stderr,
      /reason=restore_checksum_mismatch/);
    assert.equal(existsSync(target), false);

    const restored = run(target, 'restore', '--confirm', '--source', staged, '--sha256', sha256);
    assert.equal(restored.status, 0, restored.stderr);
    assert.match(restored.stdout, /^community_restore_complete schemaVersion=\d+\n$/);
    assert.equal(statSync(target).mode & 0o777, 0o600);
    const check = new DatabaseSync(target, { readOnly: true });
    assert.equal(check.prepare('SELECT keep_last FROM backup_config').get().keep_last, 7);
    check.close();

    assert.match(run(target, 'restore', '--confirm', '--source', staged, '--sha256', sha256).stderr, /reason=restore_destination_exists/);
    const corrupt = join(directory, 'corrupt.sqlite'), other = join(directory, 'other.sqlite');
    copyFileSync(staged, corrupt);
    writeFileSync(corrupt, 'not sqlite');
    const failed = run(other, 'restore', '--confirm', '--source', corrupt, '--sha256', digest(corrupt));
    assert.equal(failed.status, 1);
    assert.equal(existsSync(other), false);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
