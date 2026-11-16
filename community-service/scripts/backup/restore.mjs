import { lstat, realpath, rename, open, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify, isDeepStrictEqual } from 'node:util';
import { fileURLToPath } from 'node:url';
import { withCheckpoint } from '../operator/state.mjs';
import { CliError } from '../operator/transport.mjs';
import { protectedInput, independentDestination, durableFile, fileDigest, cleanTemporary } from './files.mjs';
import { decryptArchive } from './archive.mjs';
import { digest } from './validate.mjs';
import { validateInputs, secretNames } from './legacy-config.mjs';

const outputNames = ['configuration.json', 'restore-report.json', 'restore.sqlite', 'secrets.json'];
const json = bytes => JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));

async function privateSource(path) {
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink() || info.mode & 0o077 ||
      (process.getuid && info.uid !== process.getuid())) throw new CliError('unsafe_restore_source');
  return realpath(path);
}

async function measuredFile(directory, name) {
  const path = join(directory, name), info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink() || info.mode & 0o077 ||
      (process.getuid && info.uid !== process.getuid())) throw new CliError('unsafe_restore_output');
  return { name, bytes: info.size, sha256: await fileDigest(path) };
}

async function finish(store, directory, state) {
  if (!Array.isArray(state.files) || state.files.length !== outputNames.length ||
      state.files.map(file => file.name).sort().join(',') !== outputNames.join(',')) throw new CliError('invalid_restore_checkpoint');
  for (const file of state.files)
    if (!isDeepStrictEqual(await measuredFile(directory, file.name), file)) throw new CliError('completed_restore_corrupt');
  for (const name of ['database.sql', 'validation.json', 'restore-result.json', 'restore.partial.sqlite'])
    await rm(join(directory, name), { force: true });
  await cleanTemporary(directory);
  state.state = 'complete'; state.completedAt ??= Date.now();
  await store.write('state.json', state);
  const report = json(await protectedInput(join(directory, 'restore-report.json'), 262144));
  return { version: 1, jobId: state.jobId, state: 'complete', enabled: false, environment: 'isolated_local_disabled',
    artifacts: report.validation.artifacts, payloadBytes: report.validation.payloadBytes,
    countersRebuilt: report.counters.changed, providerUsage: null, activationAllowed: false };
}

export async function restoreJob(options, dependencies = {}) {
  if (!options.confirm) return { version: 1, dryRun: true, enabled: false, activationAllowed: false,
    required: ['completed_backup_directory_and_matched_archives', 'protected_age_identity_file',
      'new_private_local_destination', 'explicit_archive_sql_and_artifact_bounds'] };
  if (!options.source || !options.identity || !options.destination ||
      !Number.isSafeInteger(options.maximum) || options.maximum < 1 || options.maximum > 1200000000 ||
      !Number.isSafeInteger(options.archiveMaximum) || options.archiveMaximum < 100 || options.archiveMaximum > 1300000000 ||
      !options.bounds || Object.keys(options.bounds).length !== 5 ||
      ['compressedBytes', 'expandedBytes', 'events', 'text', 'depth'].some(key => !Number.isSafeInteger(options.bounds[key]) || options.bounds[key] <= 0))
    throw new CliError('restore_limits_required');
  const source = await privateSource(options.source);
  const directory = await independentDestination(options.destination);
  if (directory === source || directory.startsWith(`${source}/`)) throw new CliError('restore_destination_is_source');
  const backup = json(await protectedInput(join(source, 'state.json'), 262144));
  if (backup.version !== 1 || backup.state !== 'complete' || !/^[a-f0-9-]{36}$/.test(backup.jobId) ||
      !/^[a-f0-9]{64}$/.test(backup.scope?.inputDigest) || !Array.isArray(backup.files) || backup.files.length !== 2 ||
      backup.files.map(file => file.name).sort().join(',') !== 'secrets.tar.age,snapshot.tar.age' ||
      backup.files.some(file => !/^[a-f0-9]{64}$/.test(file.sha256) || !Number.isSafeInteger(file.bytes) ||
        file.bytes < 100 || file.bytes > options.archiveMaximum)) throw new CliError('restore_complete_backup_required');
  const scope = { source, backupJobId: backup.jobId, files: backup.files, inputDigest: backup.scope.inputDigest,
    snapshotBookmark: backup.bookmark, account: backup.scope.account, database: backup.scope.database, sqlDigest: backup.sqlDigest,
    maximum: options.maximum, archiveMaximum: options.archiveMaximum, bounds: options.bounds };
  return withCheckpoint(directory, !options.resume, async store => {
    let state = options.resume ? await store.read('state.json') : null;
    if (options.resume && (!state || state.version !== 1 || !/^[a-f0-9-]{36}$/.test(state.jobId) ||
        !['staging', 'importing', 'validated', 'complete'].includes(state.state) || !isDeepStrictEqual(state.scope, scope)))
      throw new CliError('restore_checkpoint_scope_mismatch');
    if (state && ['validated', 'complete'].includes(state.state)) return finish(store, directory, state);
    state ??= { version: 1, jobId: crypto.randomUUID(), state: 'staging', createdAt: Date.now(), scope };
    await store.write('state.json', state);
    try {
      await cleanTemporary(directory);
      const snapshot = await decryptArchive(join(source, 'snapshot.tar.age'), options.identity,
        { 'manifest.json': 262144, 'database.sql': options.maximum }, options.archiveMaximum,
        backup.files.find(file => file.name === 'snapshot.tar.age'));
      const secrets = await decryptArchive(join(source, 'secrets.tar.age'), options.identity,
        { 'secret-manifest.json': 131072, 'secrets.json': 131072 }, options.archiveMaximum,
        backup.files.find(file => file.name === 'secrets.tar.age'));
      const manifest = json(snapshot.get('manifest.json')), secretManifest = json(secrets.get('secret-manifest.json'));
      const sql = snapshot.get('database.sql'), secretBytes = secrets.get('secrets.json');
      if (manifest.format !== 'community-d1-backup-1' || secretManifest.format !== 'community-d1-secrets-1' ||
          manifest.jobId !== backup.jobId || secretManifest.jobId !== backup.jobId ||
          manifest.account !== backup.scope.account || manifest.database !== backup.scope.database ||
          manifest.snapshotBookmark !== backup.bookmark || manifest.sql?.name !== 'database.sql' ||
          manifest.sql.bytes !== sql.length || manifest.sql.bytes !== backup.cost?.sqlBytes ||
          manifest.sql.sha256 !== digest(sql) || manifest.sql.sha256 !== backup.sqlDigest ||
          !isDeepStrictEqual(manifest.bounds, backup.scope.bounds) ||
          secretManifest.inputDigest !== backup.scope.inputDigest ||
          (manifest.inputDigest !== undefined && manifest.inputDigest !== secretManifest.inputDigest) ||
          secretManifest.configurationSha256 !== digest(Buffer.from(JSON.stringify(manifest.configuration))) ||
          !isDeepStrictEqual(manifest.secretInventory, secretNames) || !isDeepStrictEqual(secretManifest.inventory, secretNames) ||
          manifest.consistency !== 'provider_full_export_query_lock' ||
          manifest.authorization !== 'restore_disabled_no_automatic_activation' ||
          manifest.configurationSource !== 'operator_supplied_not_provider_attested' ||
          !Array.isArray(manifest.validation?.inventory)) throw new CliError('restore_archive_pair_mismatch');
      await validateInputs(Buffer.from(JSON.stringify(manifest.configuration)), secretBytes,
        { account: manifest.account, database: manifest.database });
      await durableFile(directory, 'database.sql', handle => handle.writeFile(sql));
      await store.write('validation.json', { bounds: options.bounds, inventory: manifest.validation.inventory, validation: manifest.validation });
      await durableFile(directory, 'restore.partial.sqlite', handle => handle.writeFile(Buffer.alloc(0)));
      state.state = 'importing'; state.lastError = null; await store.write('state.json', state);
      try {
        await (dependencies.execute ?? promisify(execFile))(process.execPath,
          [fileURLToPath(new URL('./restore-worker.mjs', import.meta.url)), directory, String(options.maximum)],
          { timeout: 120000, maxBuffer: 8192, env: { PATH: process.env.PATH }, encoding: 'utf8' });
      } catch (error) {
        throw new CliError(/^[a-z_]{1,100}$/.test(error.stderr ?? '') ? error.stderr : 'restore_import_interrupted');
      }
      const result = json(await protectedInput(join(directory, 'restore-result.json'), 262144));
      await durableFile(directory, 'configuration.json', handle => handle.writeFile(JSON.stringify({
        version: 1, enabled: false, activationAllowed: false, mode: 'isolated_local_disabled',
        bindings: {}, vars: { APP_ENV: 'restore-disabled' }, callbacks: false, enrollment: false,
        sourceConfiguration: manifest.configuration, configurationSource: manifest.configurationSource,
      })));
      await durableFile(directory, 'secrets.json', handle => handle.writeFile(secretBytes));
      await durableFile(directory, 'restore-report.json', handle => handle.writeFile(JSON.stringify({
        version: 1, jobId: state.jobId, backupJobId: backup.jobId, snapshotBookmark: backup.bookmark,
        enabled: false, activationAllowed: false, restoredAt: Date.now(), sourceCreatedAt: manifest.createdAt,
        validation: result.counters.validation, sourceInventory: result.original.inventory, counters: result.counters,
        usage: result.usage, providerUsage: null, providerFreshness: 'unknown',
        usageProvenance: 'original_snapshot_source_and_period_not_current_provider_totals',
        network: 'no_bindings_callbacks_clients_or_provider_credentials',
        cacheIdentity: `isolated-restore:${state.jobId}`, migrationsApplied: [],
        cost: { applicationWorkerRequests: 0, providerRequests: 0, providerD1Reads: null, providerD1Writes: null },
        hostedAcceptance: 'unverified_separate_authorization_required',
      })));
      const restored = await open(join(directory, 'restore.partial.sqlite'), 'r');
      try { await restored.sync(); } finally { await restored.close(); }
      await rename(join(directory, 'restore.partial.sqlite'), join(directory, 'restore.sqlite'));
      const folder = await open(directory, 'r'); try { await folder.sync(); } finally { await folder.close(); }
      state.files = await Promise.all(outputNames.map(name => measuredFile(directory, name)));
      state.state = 'validated'; await store.write('state.json', state);
      return finish(store, directory, state);
    } catch (error) {
      state.lastError = error instanceof CliError ? error.code : 'restore_failed';
      await store.write('state.json', state);
      throw error instanceof CliError ? error : new CliError('restore_failed');
    }
  });
}