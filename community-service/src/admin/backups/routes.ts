import { createReadStream } from 'node:fs';
import { basename } from 'node:path';
import { Readable } from 'node:stream';
import { z } from 'zod';
import type { Env } from '../../config';
import type { SqlDatabase } from '../../runtime/contracts';
import { BodyLimitError, readBoundedBytes } from '../../http/bounded-body';
import { operatorFailure as failure, operatorReadAdmission } from '../admission';
import { backupRoot, confinedDirectory, destinationState, normalizeDestination } from '../../backups/destination';
import { BackupError } from '../../backups/errors';
import { backupRunning, startBackup } from '../../backups/runner';
import { nextBackupAt } from '../../backups/schedule';
import { stageBackup, verifiedBackupFile } from '../../backups/staging';
import { apiConfig, apiRun, backupAudit, listBackupRuns, readBackupConfig, saveBackupConfig } from '../../backups/store';

const configInput = z.strictObject({ enabled: z.boolean(), frequency: z.enum(['hourly', 'every_6h', 'daily', 'weekly']),
  atUtc: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/), keepLast: z.number().int().min(1).max(365),
  keepWeekly: z.number().int().min(0).max(104), destination: z.string().max(512), integrityCheck: z.boolean() });
const testInput = z.strictObject({ destination: z.string().max(512).optional() });
const runPath = /^\/admin\/v1\/backups\/([0-9a-f-]{36})\/(download|stage)$/;

class InvalidRequest extends Error {}

async function jsonBody(request: Request, limit: number, required: boolean): Promise<unknown> {
  const bytes = await readBoundedBytes(request, limit);
  if (!bytes.length) { if (required) throw new InvalidRequest(); return undefined; }
  if (request.headers.get('content-type') !== 'application/json') throw new InvalidRequest();
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw new InvalidRequest(); }
}

/** Run and stage take no input: an empty body or `{}`. */
async function emptyBody(request: Request, limit: number): Promise<void> {
  const body = await jsonBody(request, limit, false);
  if (body !== undefined && (typeof body !== 'object' || body === null || Array.isArray(body) || Object.keys(body).length))
    throw new InvalidRequest();
}

async function view(env: Env, db: SqlDatabase, nowMs: number) {
  const config = await readBackupConfig(db);
  const destination = await destinationState(env.COMMUNITY_BACKUP_ROOT, config.destination, false);
  const next = destination.root ? nextBackupAt(config, nowMs) : null;
  return { version: 1, observedAt: nowMs, config: apiConfig(config), destination,
    runs: (await listBackupRuns(db, 20)).map(apiRun), next: next === null ? null : { at: next }, running: backupRunning() };
}

async function rootOrFail(env: Env): Promise<string> {
  const root = await backupRoot(env.COMMUNITY_BACKUP_ROOT);
  if (!root) throw new BackupError('backups_not_configured', 503);
  return root;
}

async function saveConfig(request: Request, env: Env, db: SqlDatabase, subject: string, limit: number, nowMs: number) {
  const parsed = configInput.safeParse(await jsonBody(request, limit, true));
  if (!parsed.success) throw new InvalidRequest();
  const input = parsed.data, root = await rootOrFail(env);
  const destination = normalizeDestination(input.destination, root, env.COMMUNITY_BACKUP_ROOT);
  await confinedDirectory(root, destination, true);
  const state = await destinationState(env.COMMUNITY_BACKUP_ROOT, destination, true);
  if (!state.writable) throw new BackupError(state.error === 'destination_full' ? 'backup_destination_full' : 'backup_destination_unwritable');
  const saved = { enabled: input.enabled ? 1 : 0, frequency: input.frequency, at_utc: input.atUtc, keep_last: input.keepLast,
    keep_weekly: input.keepWeekly, destination, integrity_check: input.integrityCheck ? 1 : 0, updated_at: nowMs };
  await db.batch([saveBackupConfig(db, saved, subject),
    backupAudit(db, subject, 'backup_configure', 'config', 'Updated the backup configuration', { ...input, destination }, nowMs)]);
}

async function download(env: Env, db: SqlDatabase, runId: string): Promise<Response> {
  const { run, path } = await verifiedBackupFile(env, db, runId);
  const stream = Readable.toWeb(createReadStream(path)) as ReadableStream<Uint8Array>;
  return new Response(stream, { headers: { 'Content-Type': 'application/vnd.sqlite3', 'Content-Length': String(run.bytes),
    'Content-Disposition': `attachment; filename="${basename(path)}"`, 'X-Backup-Sha256': run.sha256!,
    'Cache-Control': 'private, no-store' } });
}

/**
 * Serve the operator backup API under `/admin/v1/backups`. Mutations arrive here only after the
 * operator CSRF checks; configuration, manual runs and staging each write an audit row.
 */
export async function operatorBackups(request: Request, env: Env, subject: string, nowMs: number): Promise<Response> {
  return operatorReadAdmission(env, Math.floor(nowMs / 1000), async (db, requestBytes) => {
    const url = new URL(request.url), path = url.pathname, method = request.method;
    const store = env.CATALOG_DB!;
    try {
      if (url.search || request.headers.has('content-encoding')) throw new InvalidRequest();
      if (path === '/admin/v1/backups') {
        if (method !== 'GET') return failure(405, 'method_not_allowed');
        return Response.json(await view(env, db, nowMs));
      }
      if (path === '/admin/v1/backups/config') {
        if (method !== 'PUT') return failure(405, 'method_not_allowed');
        await saveConfig(request, env, store, subject, requestBytes, nowMs);
        return Response.json(await view(env, db, nowMs));
      }
      if (path === '/admin/v1/backups/run') {
        if (method !== 'POST') return failure(405, 'method_not_allowed');
        await emptyBody(request, requestBytes);
        const runId = await startBackup(env, 'manual', subject, nowMs);
        return Response.json({ version: 1, observedAt: nowMs, runId, status: 'running' }, { status: 202 });
      }
      if (path === '/admin/v1/backups/test-destination') {
        if (method !== 'POST') return failure(405, 'method_not_allowed');
        const parsed = testInput.safeParse(await jsonBody(request, requestBytes, false) ?? {});
        if (!parsed.success) throw new InvalidRequest();
        const root = await rootOrFail(env);
        const destination = normalizeDestination(parsed.data.destination ?? (await readBackupConfig(db)).destination,
          root, env.COMMUNITY_BACKUP_ROOT);
        return Response.json({ version: 1, observedAt: nowMs,
          destination: await destinationState(env.COMMUNITY_BACKUP_ROOT, destination, true) });
      }
      const match = path.match(runPath);
      if (!match) return failure(404, 'not_found');
      if (match[2] === 'download') {
        if (method !== 'GET') return failure(405, 'method_not_allowed');
        return await download(env, db, match[1]);
      }
      if (method !== 'POST') return failure(405, 'method_not_allowed');
      await emptyBody(request, requestBytes);
      return Response.json(await stageBackup(env, store, match[1], subject, nowMs));
    } catch (error) {
      if (error instanceof BackupError) return failure(error.status, error.code);
      if (error instanceof BodyLimitError) return failure(413, 'body_too_large');
      if (error instanceof InvalidRequest) return failure(400, path === '/admin/v1/backups/config' ? 'invalid_backup_config' : 'invalid_backup_request');
      throw error;
    }
  });
}
