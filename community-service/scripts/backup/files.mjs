import { constants, createReadStream, createWriteStream } from 'node:fs';
import { open, rename, unlink, realpath, stat, readdir, lstat } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { pipeline } from 'node:stream/promises';
import { randomUUID } from 'node:crypto';
import { CliError } from '../operator/transport.mjs';

export async function protectedInput(path, maximum = 131072) {
  let handle;
  try {
    handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const info = await handle.stat();
    if (!info.isFile() || info.size > maximum || info.mode & 0o077 ||
        (process.getuid && info.uid !== process.getuid())) throw new Error();
    const bytes = await handle.readFile();
    if (bytes.length > maximum) throw new Error();
    return bytes;
  } catch { throw new CliError('unsafe_backup_input'); }
  finally { await handle?.close(); }
}

export async function independentDestination(path) {
  const parent = await realpath(dirname(resolve(path)));
  const destination = join(parent, resolve(path).split(sep).at(-1));
  const checkout = await realpath(new URL('../../../', import.meta.url));
  if (destination === checkout || destination.startsWith(`${checkout}${sep}`)) throw new CliError('backup_destination_inside_checkout');
  return destination;
}

export async function durableFile(directory, name, write) {
  const temporary = join(directory, `.backup-${randomUUID()}`);
  let handle;
  try {
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    await write(handle);
    await handle.sync(); await handle.close(); handle = undefined;
    await rename(temporary, join(directory, name));
    const folder = await open(directory, constants.O_RDONLY);
    try { await folder.sync(); } finally { await folder.close(); }
  } finally { await handle?.close(); await unlink(temporary).catch(() => {}); }
}

export async function cleanTemporary(directory) {
  for (const name of await readdir(directory)) {
    if (!/^\.(?:backup|encrypted)-[a-f0-9-]{36}$/.test(name)) continue;
    const path = join(directory, name);
    const info = await lstat(path);
    if (!info.isFile() || info.isSymbolicLink() || info.mode & 0o077 ||
        (process.getuid && info.uid !== process.getuid())) throw new CliError('unsafe_backup_staging');
    await unlink(path);
  }
}

function finished(child) {
  child.stderr.resume();
  return new Promise((resolveResult, reject) => {
    child.on('error', () => reject(new CliError('backup_encryption_failed')));
    child.on('close', code => code === 0 ? resolveResult() : reject(new CliError('backup_encryption_failed')));
  });
}

export async function encryptArchive(directory, files, recipient, output) {
  const temporary = join(directory, `.encrypted-${randomUUID()}`);
  const tar = spawn('tar', ['--format=ustar', '-cf', '-', '-C', directory, ...files],
    { stdio: ['ignore', 'pipe', 'pipe'], timeout: 120000, env: { ...process.env, COPYFILE_DISABLE: '1' } });
  const age = spawn('age', ['--encrypt', '--recipient', recipient], { stdio: ['pipe', 'pipe', 'pipe'], timeout: 120000 });
  try {
    await Promise.all([finished(tar), finished(age), pipeline(tar.stdout, age.stdin),
      pipeline(age.stdout, createWriteStream(temporary, { flags: 'wx', mode: 0o600 }))]);
    const file = await open(temporary, constants.O_RDONLY | constants.O_NOFOLLOW);
    try { await file.sync(); } finally { await file.close(); }
    if ((await stat(temporary)).size < 100) throw new Error();
    await rename(temporary, join(directory, output));
    const folder = await open(directory, constants.O_RDONLY);
    try { await folder.sync(); } finally { await folder.close(); }
  } catch { throw new CliError('backup_encryption_failed'); }
  finally { tar.kill(); age.kill(); await unlink(temporary).catch(() => {}); }
}

export async function fileDigest(path) {
  const { createHash } = await import('node:crypto');
  const hash = createHash('sha256');
  const stream = createReadStream(path, { flags: constants.O_RDONLY | constants.O_NOFOLLOW });
  for await (const bytes of stream) hash.update(bytes);
  return hash.digest('hex');
}