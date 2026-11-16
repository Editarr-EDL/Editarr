import { DatabaseSync, constants } from 'node:sqlite';
import { readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { verifyArtifactSignature, withoutArrProvenance } from './artifact-runtime.mjs';
import { CliError } from '../operator/transport.mjs';

export const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const migrationsRoot = new URL('../../migrations/', import.meta.url);
const legacySchemaVersion = 14;
const quote = name => `"${name.replaceAll('"', '""')}"`;

export async function knownSchema() {
  const files = (await readdir(migrationsRoot)).filter(name => /^\d{4}_.+\.sql$/.test(name)
    && Number(name.slice(0, 4)) <= legacySchemaVersion).sort();
  if (files.length !== legacySchemaVersion || files.some((name, index) => Number(name.slice(0, 4)) !== index + 1))
    throw new CliError('unsupported_backup_schema');
  const db = new DatabaseSync(':memory:');
  try {
    for (const name of files) db.exec(await readFile(new URL(name, migrationsRoot), 'utf8'));
    return { files, objects: schemaObjects(db) };
  } finally { db.close(); }
}

function schemaObjects(db) {
  return db.prepare("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' AND name != 'd1_migrations' ORDER BY type,name").all();
}

function rows(db, sql) {
  const statement = db.prepare(sql);
  statement.setReadBigInts(true);
  return statement.iterate();
}

function inventory(db) {
  const tables = db.prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB '_cf_*' ORDER BY name").all();
  return tables.map(({ name }) => {
    const columns = db.prepare(`PRAGMA table_info(${quote(name)})`).all();
    const order = columns.filter(column => column.pk).sort((left, right) => left.pk - right.pk).map(column => quote(column.name));
    const hash = createHash('sha256');
    let count = 0;
    for (const row of rows(db, `SELECT * FROM ${quote(name)} ORDER BY ${order.length ? order.join(',') : 'rowid'}`)) {
      const value = Object.values(row).map(item => item instanceof Uint8Array ? ['blob', Buffer.from(item).toString('base64')]
        : typeof item === 'bigint' ? ['integer', item.toString()] : [typeof item, item]);
      const bytes = Buffer.from(JSON.stringify(value));
      const length = Buffer.alloc(4); length.writeUInt32BE(bytes.length);
      hash.update(length).update(bytes); count++;
    }
    return { table: name, rows: count, sha256: hash.digest('hex') };
  });
}

export async function validateDatabase(db, bounds, expectedInventory) {
  if (!bounds || Object.values(bounds).length !== 5 ||
      ['compressedBytes', 'expandedBytes', 'events', 'text', 'depth'].some(key => !Number.isSafeInteger(bounds[key]) || bounds[key] <= 0))
    throw new CliError('artifact_bounds_required');
  const known = await knownSchema();
  if (!isDeepStrictEqual(schemaObjects(db), known.objects)) throw new CliError('unsupported_backup_schema');
  let revisions;
  try {
    if (db.prepare("SELECT type FROM sqlite_schema WHERE name='d1_migrations'").get()?.type !== 'table' ||
        db.prepare('PRAGMA table_info(d1_migrations)').all().map(column => column.name).join(',') !== 'id,name,applied_at')
      throw new Error();
    revisions = db.prepare('SELECT name FROM d1_migrations ORDER BY name').all().map(row => row.name);
  }
  catch { throw new CliError('migration_inventory_missing'); }
  if (!isDeepStrictEqual(revisions, known.files)) throw new CliError('unknown_migration_revision');
  if (db.prepare('PRAGMA integrity_check').get().integrity_check !== 'ok' || db.prepare('PRAGMA foreign_key_check').all().length)
    throw new CliError('backup_integrity_failed');
  let payloadBytes = 0;
  let artifacts = 0;
  const storage = new Map([['global', 0]]);
  for (const row of db.prepare(`SELECT r.*, p.artifact, p.manifest, p.envelope,
    p.artifact_digest AS stored_artifact_digest, p.manifest_digest AS stored_manifest_digest,
    p.envelope_digest AS stored_envelope_digest, k.public_key, k.state AS key_state,
    i.contributor_id AS key_contributor FROM contribution_revisions r LEFT JOIN artifact_payloads p USING(object_key)
    JOIN installation_keys k USING(key_id) JOIN installations i USING(installation_id) ORDER BY revision_sequence`).iterate()) {
    for (const part of ['artifact', 'manifest', 'envelope']) {
      if (!(row[part] instanceof Uint8Array) || row[part].length !== row[`${part}_bytes`] ||
          digest(row[part]) !== row[`${part}_digest`] || row[`stored_${part}_digest`] !== row[`${part}_digest`])
        throw new CliError('backup_payload_mismatch');
    }
    if (row.key_contributor !== row.contributor_id) throw new CliError('backup_key_owner_mismatch');
    let verified;
    try {
      verified = await verifyArtifactSignature(row.artifact, row.manifest, row.envelope,
        { contributorId: row.contributor_id, keyId: row.key_id, publicKey: row.public_key,
          status: 'retired' }, row.revision_id, bounds, true);
    } catch { throw new CliError('backup_signature_invalid'); }
    if (!isDeepStrictEqual(verified.artifact.summary, withoutArrProvenance(JSON.parse(row.summary_json))) ||
        verified.artifact.artifact.schema_version !== row.artifact_schema ||
        verified.artifact.artifact.kind !== row.kind || verified.artifact.summary.total !== row.total_events ||
        (verified.artifact.summary.channels.audio ?? 0) !== row.audio_events ||
        (verified.artifact.summary.channels.video ?? 0) !== row.video_events) throw new CliError('backup_summary_mismatch');
    const bytes = row.artifact.length + row.manifest.length + row.envelope.length;
    payloadBytes += bytes; artifacts++;
    storage.set('global', payloadBytes);
    const scope = `contributor:${row.contributor_id}`;
    storage.set(scope, (storage.get(scope) ?? 0) + bytes);
    const operation = db.prepare('SELECT state,result FROM operations WHERE contributor_id=? AND kind=? AND operation_id=?')
      .get(row.contributor_id, row.operation_kind, row.operation_id);
    const publication = db.prepare('SELECT state FROM contribution_publications WHERE revision_id=?').get(row.revision_id);
    if (!operation || !publication) throw new CliError('backup_publication_link_missing');
    if (operation.state === 'completed') {
      const result = JSON.parse(operation.result);
      if (result.version !== 1 || !['published', 'quarantined', 'withdrawn'].includes(publication.state) ||
          result.revisionId !== row.revision_id || result.contributionId !== row.contribution_id ||
          result.artifactDigest !== row.artifact_digest || result.operationId !== row.operation_id)
        throw new CliError('backup_operation_link_invalid');
    } else if (['published', 'quarantined', 'withdrawn'].includes(publication.state))
      throw new CliError('backup_operation_link_invalid');
  }
  for (const operation of db.prepare("SELECT * FROM operations WHERE kind='publication' AND state='completed'").iterate()) {
    if (!db.prepare('SELECT 1 FROM contribution_revisions WHERE contributor_id=? AND operation_kind=? AND operation_id=?')
      .get(operation.contributor_id, operation.kind, operation.operation_id)) throw new CliError('backup_operation_link_invalid');
  }
  const recorded = new Map(db.prepare('SELECT scope_id,bytes FROM artifact_storage_usage').all().map(row => [row.scope_id, row.bytes]));
  for (const [scope, bytes] of storage) if ((recorded.get(scope) ?? 0) !== bytes) throw new CliError('backup_storage_counter_mismatch');
  for (const [scope, bytes] of recorded) if ((storage.get(scope) ?? 0) !== bytes) throw new CliError('backup_storage_counter_mismatch');
  const policy = db.prepare('SELECT global_bytes,contributor_bytes FROM artifact_storage_policy WHERE policy_id=1').get();
  if (!policy || payloadBytes > policy.global_bytes || [...storage].some(([scope, bytes]) => scope !== 'global' && bytes > policy.contributor_bytes))
    throw new CliError('backup_storage_policy_invalid');
  const result = { schemaRevision: known.files.at(-1), inventory: inventory(db), artifacts, payloadBytes,
    sqliteBytes: db.prepare('PRAGMA page_count').get().page_count * db.prepare('PRAGMA page_size').get().page_size,
    sqliteSizeSource: 'isolated_restore_not_hosted_database_size',
    signaturePolicy: 'cryptographic_attribution_only_preserve_key_restrictions',
    providerUsage: null, usageProvenance: 'snapshot_application_observations_not_current_provider_totals' };
  if (expectedInventory && !isDeepStrictEqual(result.inventory, expectedInventory)) throw new CliError('backup_inventory_mismatch');
  return result;
}

export async function restoreAndValidate(sql, path, bounds, expectedInventory) {
  const db = new DatabaseSync(path, { allowExtension: false, enableForeignKeyConstraints: false });
  try {
    db.setAuthorizer((action, first, second) => {
      if ([constants.SQLITE_ATTACH, constants.SQLITE_DETACH, constants.SQLITE_CREATE_VTABLE].includes(action) ||
          (action === constants.SQLITE_FUNCTION && second === 'load_extension') ||
          (action === constants.SQLITE_PRAGMA && !['foreign_keys', 'defer_foreign_keys'].includes(first)))
        return constants.SQLITE_DENY;
      return constants.SQLITE_OK;
    });
    try { db.exec(new TextDecoder('utf-8', { fatal: true }).decode(sql)); }
    catch { throw new CliError('backup_sql_restore_failed'); }
    db.setAuthorizer(null);
    return await validateDatabase(db, bounds, expectedInventory);
  } finally { db.close(); }
}