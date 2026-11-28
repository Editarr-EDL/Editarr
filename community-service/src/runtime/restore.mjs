import { chmodSync, closeSync, constants, copyFileSync, createReadStream, existsSync, fsyncSync, openSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';
import { migrationsDirectory, openDatabase, verifySnapshot } from './schema.mjs';

/** Stream a file's SHA-256 as lowercase hex. */
export async function fileSha256(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}

function fsyncPath(path) {
  const descriptor = openSync(path, 'r');
  try { fsyncSync(descriptor); } finally { closeSync(descriptor); }
}

/**
 * Offline restore of a staged snapshot into an absent database path (a new data volume). The
 * source must match `sha256` and pass schema/integrity verification; the copy is re-hashed and
 * opened with the store checks before it is kept. Existing files are never overwritten, and a
 * failed copy made by this call is removed.
 */
export async function restoreDatabase(source, path, sha256, root = migrationsDirectory) {
  if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error('restore_checksum_invalid');
  if (existsSync(path)) throw new Error('restore_destination_exists');
  verifySnapshot(source, { root });
  if (await fileSha256(source) !== sha256) throw new Error('restore_checksum_mismatch');
  copyFileSync(source, path, constants.COPYFILE_EXCL);
  try {
    chmodSync(path, 0o600);
    if (await fileSha256(path) !== sha256) throw new Error('restore_checksum_mismatch');
    fsyncPath(path);
    fsyncPath(dirname(path));
    const { database, version, inventory } = openDatabase(path, root, true);
    database.close();
    return { version, current: inventory.length };
  } catch (error) {
    for (const suffix of ['', '-wal', '-shm']) rmSync(`${path}${suffix}`, { force: true });
    throw error;
  }
}
