import { constants } from 'node:fs';
import { mkdir, open, rename, unlink, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { CliError } from './transport.mjs';

async function privateDirectory(directory, create) {
  if (create) await mkdir(directory, { mode: 0o700 });
  const info = await lstat(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || (info.mode & 0o077) ||
      (process.getuid && info.uid !== process.getuid())) throw new CliError('unsafe_checkpoint');
}

export async function withCheckpoint(directory, create, operation) {
  let lock;
  try {
    await privateDirectory(directory, create);
    lock = await open(join(directory, 'lock'), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL |
      constants.O_NOFOLLOW, 0o600);
    await lock.writeFile(JSON.stringify({ pid: process.pid }));
    await lock.sync();
    return await operation({
      read: name => readPrivate(join(directory, name)),
      write: (name, value) => writePrivate(directory, name, value),
    });
  } catch (error) {
    if (error instanceof CliError) throw error;
    throw new CliError(error.code === 'EEXIST' ? 'checkpoint_exists_or_locked' : 'checkpoint_io_failed');
  } finally {
    if (lock) { await lock.close(); await unlink(join(directory, 'lock')); }
  }
}

async function readPrivate(path) {
  let handle;
  try {
    handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await handle.stat();
    if (!info.isFile() || info.size > 262144 || (info.mode & 0o077) ||
        (process.getuid && info.uid !== process.getuid())) throw new CliError('unsafe_checkpoint');
    return JSON.parse(await handle.readFile('utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    if (error instanceof CliError) throw error;
    throw new CliError('invalid_checkpoint');
  } finally { await handle?.close(); }
}

async function writePrivate(directory, name, value) {
  const temporary = join(directory, `.tmp-${randomUUID()}`);
  let handle;
  try {
    const bytes = JSON.stringify(value);
    if (Buffer.byteLength(bytes) > 262144) throw new CliError('checkpoint_too_large');
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await handle.writeFile(bytes);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await rename(temporary, join(directory, name));
    const folder = await open(directory, constants.O_RDONLY);
    try { await folder.sync(); } finally { await folder.close(); }
  } finally {
    await handle?.close();
    await unlink(temporary).catch(() => {});
  }
}