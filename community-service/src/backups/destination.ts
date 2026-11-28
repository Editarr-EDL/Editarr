import { promises as fs } from 'node:fs';
import { isAbsolute, join, sep } from 'node:path';
import { BackupError } from './errors';

/** Directory under the backup root that receives copies staged for offline restore. */
export const STAGING_DIRECTORY = 'staged';
const segment = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/;
const notFound = (error: unknown) => (error as { code?: string }).code === 'ENOENT';

export interface DestinationState {
  configured: boolean; writable: boolean; freeBytes: number | null;
  root: string | null; path: string | null; error: string | null;
}

/**
 * Canonical `COMMUNITY_BACKUP_ROOT` (realpath of an absolute, existing directory). Null when unset,
 * which disables backups; a set but unusable root throws `backup_root_invalid`.
 */
export async function backupRoot(value: string | undefined): Promise<string | null> {
  if (!value) return null;
  if (!isAbsolute(value)) throw new BackupError('backup_root_invalid', 503);
  try {
    const real = await fs.realpath(value);
    if (!(await fs.stat(real)).isDirectory()) throw new Error();
    return real;
  } catch { throw new BackupError('backup_root_invalid', 503); }
}

/**
 * Normalize an operator destination to a path relative to the root ('' is the root itself). Absolute
 * input must name the root or a descendant; `..`, hidden or odd segments and the staging directory
 * are refused so the stored value can never point outside the root.
 */
export function normalizeDestination(value: string, root: string, configuredRoot = root): string {
  let rest = value.trim();
  if (rest.length > 512 || rest.includes('\0') || rest.includes('\\')) throw new BackupError('backup_destination_invalid');
  if (isAbsolute(rest)) {
    const base = [root, configuredRoot].find(item => rest === item || rest.startsWith(`${item.replace(/\/+$/, '')}/`));
    if (!base) throw new BackupError('backup_destination_outside_root');
    rest = rest.slice(base.replace(/\/+$/, '').length);
  }
  const parts = rest.split('/').filter(Boolean);
  if (parts.some(part => part === '..')) throw new BackupError('backup_destination_outside_root');
  if (parts.length > 4 || parts.some(part => !segment.test(part)) || parts[0] === STAGING_DIRECTORY)
    throw new BackupError('backup_destination_invalid');
  return parts.join('/');
}

/**
 * Resolve `root/relative` to a real directory confined to the root. Missing components are created
 * with mode 0700 when `create` is set; any symlinked component or a realpath outside the root is
 * refused.
 */
export async function confinedDirectory(root: string, relative: string, create: boolean): Promise<string> {
  let current = root;
  for (const part of relative ? relative.split('/') : []) {
    current = join(current, part);
    let stat = await fs.lstat(current).catch(error => { if (notFound(error)) return null; throw error; });
    if (!stat) {
      if (!create) throw new BackupError('backup_destination_missing');
      await fs.mkdir(current, { mode: 0o700 }).catch(error => { if ((error as { code?: string }).code !== 'EEXIST') throw error; });
      stat = await fs.lstat(current);
      if (stat.isDirectory() && !stat.isSymbolicLink()) await fs.chmod(current, 0o700);
    }
    if (stat.isSymbolicLink()) throw new BackupError('backup_destination_symlink');
    if (!stat.isDirectory()) throw new BackupError('backup_destination_invalid');
  }
  const real = await fs.realpath(current);
  if (real !== root && !real.startsWith(root + sep)) throw new BackupError('backup_destination_outside_root');
  return real;
}

/**
 * Resolve a stored root-relative backup file, refusing symlinks, non-files and anything whose
 * realpath leaves the root.
 */
export async function confinedFile(root: string, relative: string): Promise<string> {
  const parts = relative.split('/');
  const name = parts.pop() ?? '';
  if (!segment.test(name)) throw new BackupError('backup_destination_invalid');
  const directory = await confinedDirectory(root, parts.join('/'), false);
  const path = join(directory, name);
  const stat = await fs.lstat(path);
  if (stat.isSymbolicLink() || !stat.isFile()) throw new BackupError('backup_destination_invalid');
  return path;
}

async function freeBytes(path: string): Promise<number | null> {
  try { const stat = await fs.statfs(path); return Number(stat.bavail) * Number(stat.bsize); }
  catch { return null; }
}

/** fsync a file or directory so a completed backup survives power loss. */
export async function fsyncPath(path: string): Promise<void> {
  const handle = await fs.open(path, 'r');
  try { await handle.sync(); } finally { await handle.close(); }
}

/**
 * Describe the destination. Reads check access only; `probe` (test-destination and saves) creates
 * the directory and writes, fsyncs and removes a 0600 probe file.
 */
export async function destinationState(rootValue: string | undefined, destination: string, probe: boolean): Promise<DestinationState> {
  let root: string | null;
  try { root = await backupRoot(rootValue); }
  catch (error) { return { configured: false, writable: false, freeBytes: null, root: null, path: null, error: (error as BackupError).code }; }
  if (!root) return { configured: false, writable: false, freeBytes: null, root: null, path: null, error: 'backups_not_configured' };
  const state: DestinationState = { configured: true, writable: false, freeBytes: null, root, path: null, error: null };
  try {
    const directory = await confinedDirectory(root, normalizeDestination(destination, root, rootValue), probe);
    state.path = directory;
    state.freeBytes = await freeBytes(directory);
    if (probe) {
      const file = join(directory, `.editarr-probe-${crypto.randomUUID()}`);
      const handle = await fs.open(file, 'wx', 0o600);
      try { await handle.writeFile('editarr backup probe\n'); await handle.sync(); }
      finally { await handle.close(); await fs.rm(file, { force: true }); }
    } else await fs.access(directory, fs.constants.W_OK);
    state.writable = true;
  } catch (error) {
    if (error instanceof BackupError) state.error = error.code;
    else {
      const errno = (error as { code?: string }).code;
      state.error = errno === 'ENOSPC' || errno === 'EDQUOT' ? 'destination_full' : 'destination_unwritable';
    }
  }
  return state;
}
