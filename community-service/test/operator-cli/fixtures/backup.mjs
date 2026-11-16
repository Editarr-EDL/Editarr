import { DatabaseSync } from 'node:sqlite';
import { readFile, mkdtemp, writeFile, mkdir, rm, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { promisify } from 'node:util';
import { execFile } from 'node:child_process';
import { createPrivateKey, createPublicKey } from 'node:crypto';
import { knownSchema, digest, restoreAndValidate } from '../../../scripts/backup/validate.mjs';
import { configurationVariables, validateInputs, secretNames } from '../../../scripts/backup/legacy-config.mjs';
import { encryptArchive } from '../../../scripts/backup/files.mjs';

export const bounds = { compressedBytes: 1900000, expandedBytes: 4000000, events: 10000, text: 8192, depth: 64 };
export const account = 'a'.repeat(32);
export const database = '12345678-1234-1234-1234-123456789abc';
export const token = 'synthetic_protected_token_12345';

export async function emptyFixture() {
  const db = new DatabaseSync(':memory:');
  const known = await knownSchema();
  for (const file of known.files) db.exec(await readFile(new URL(`../../../migrations/${file}`, import.meta.url), 'utf8'));
  db.exec('CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL)');
  for (const file of known.files) db.prepare('INSERT INTO d1_migrations(name) VALUES (?)').run(file);
  return db;
}

export async function signedFixture(target, prepareRelease) {
  const db = target ?? await emptyFixture();
  const fixture = JSON.parse(await readFile(new URL('../../../../tests/fixtures/community/donation-artifacts-v2.json',
    import.meta.url), 'utf8'));
  const bytes = Object.fromEntries(Object.entries(fixture.cases.ai).map(([key, value]) => [key, Buffer.from(value, 'base64')]));
  const manifest = JSON.parse(bytes.manifest);
  db.prepare("INSERT INTO contributors(contributor_id,discord_user_id,display_name,created_at,display_name_key) VALUES ('contributor-1','discord-1','Public Name',1,'public name')").run();
  db.exec("INSERT INTO installations VALUES ('installation-1','contributor-1',1)");
  db.prepare("INSERT INTO installation_keys VALUES ('key-1','installation-1','Ed25519',?,'active',1,NULL)").run(Buffer.from(fixture.public_key, 'base64'));
  db.exec("INSERT INTO works VALUES ('work-1','movie',1); INSERT INTO releases VALUES ('release-1','work-1','{}',NULL,NULL,10,1)");
  prepareRelease?.(db);
  db.exec("INSERT INTO contributions VALUES ('contribution-1','contributor-1',1); INSERT INTO operations VALUES ('contributor-1','publication','operation-1','digest','pending',NULL,1,1)");
  db.prepare(`INSERT INTO contribution_revisions(revision_id,contribution_id,contributor_id,operation_kind,operation_id,
    key_id,release_id,kind,artifact_schema,artifact_digest,manifest_digest,envelope_digest,artifact_bytes,manifest_bytes,
    envelope_bytes,object_key,summary_json,total_events,audio_events,video_events,lineage_depth,parent_limit,created_at)
    VALUES ('submission-ai','contribution-1','contributor-1','publication','operation-1','key-1','release-1','scan',
    'community-artifact-2',?,?,?,?,?,?,'object-1',?,1,1,0,0,4,1)`)
    .run(digest(bytes.artifact), digest(bytes.manifest), digest(bytes.envelope), bytes.artifact.length, bytes.manifest.length,
      bytes.envelope.length, JSON.stringify(manifest.summary));
  db.exec("INSERT INTO contribution_publications VALUES ('submission-ai','reserved',NULL,1); INSERT INTO contribution_statistics(revision_id) VALUES ('submission-ai')");
  db.prepare('INSERT INTO artifact_payloads VALUES (?,?,?,?,?,?,?)').run('object-1', bytes.artifact, bytes.manifest, bytes.envelope,
    digest(bytes.artifact), digest(bytes.manifest), digest(bytes.envelope));
  db.prepare("UPDATE contribution_publications SET state='object_stored',object_etag=?,updated_at=2 WHERE revision_id='submission-ai'").run(digest(bytes.artifact));
  db.exec("UPDATE contribution_publications SET state='published',updated_at=3 WHERE revision_id='submission-ai'");
  db.prepare("UPDATE operations SET state='completed',result=? WHERE operation_id='operation-1'").run(JSON.stringify({
    version: 1, operationId: 'operation-1', contributionId: 'contribution-1', revisionId: 'submission-ai',
    artifactDigest: digest(bytes.artifact), committedAt: 3,
  }));
  db.exec("UPDATE installation_keys SET state='retired',retired_at=4 WHERE key_id='key-1'");
  return { db, bytes };
}

export function dump(db) {
  const schema = db.prepare("SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name").all();
  const parts = schema.filter(row => row.type === 'table').map(row => `${row.sql};`);
  for (const { name } of [...schema.filter(row => row.type === 'table'), { name: 'sqlite_sequence' }]) {
    if (name === 'sqlite_sequence') parts.push('DELETE FROM sqlite_sequence;');
    const statement = db.prepare(`SELECT * FROM "${name}"`);
    statement.setReadBigInts(true);
    for (const row of statement.iterate()) {
      const values = Object.values(row).map(value => db.prepare('SELECT quote(?) AS value').get(value).value);
      parts.push(`INSERT INTO "${name}" VALUES (${values.join(',')});`);
    }
  }
  parts.push(...schema.filter(row => row.type !== 'table').map(row => `${row.sql};`));
  return Buffer.from(parts.join('\n'));
}

export async function jobInputs() {
  const directory = await mkdtemp(join(tmpdir(), 'community-legacy-archive-'));
  const execute = promisify(execFile);
  const identity = join(directory, 'age.key');
  await execute('age-keygen', ['-o', identity]);
  const { stdout } = await execute('age-keygen', ['-y', identity]);
  const privateKey = createPrivateKey({ key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'),
    Buffer.from(Array.from({ length: 32 }, (_, index) => index))]), format: 'der', type: 'pkcs8' });
  const publicKey = createPublicKey(privateKey).export({ type: 'spki', format: 'der' }).subarray(-32);
  const ring = [{ keyId: `sha256:${digest(publicKey)}`, publicKey: publicKey.toString('base64'), status: 'active' }];
  const config = join(directory, 'config.json'), secrets = join(directory, 'secret-input.json');
  await writeFile(config, JSON.stringify({ deployment: { releaseVersion: 'test', revision: 'synthetic', workerVersion: 'synthetic' },
    bindings: { CATALOG_DB: { accountId: account, databaseId: database }, OPERATOR_ASSETS: null,
      OAUTH_IP_LIMITER: null, OAUTH_SHARED_LIMITER: null },
    vars: { ...Object.fromEntries(configurationVariables.map(name => [name, null])),
      APP_ENV: 'local', DOWNLOAD_RECEIPT_KEYS: JSON.stringify(ring) } }), { mode: 0o600 });
  await writeFile(secrets, JSON.stringify({ DISCORD_BOT_TOKEN: 'synthetic-bot-secret', DISCORD_CLIENT_SECRET: 'synthetic-oauth-secret',
    DOWNLOAD_RECEIPT_PRIVATE_KEY: privateKey.export({ type: 'pkcs8', format: 'der' }).toString('base64') }), { mode: 0o600 });
  return { directory, identity, options: { account, database, config, secrets, recipient: stdout.trim(),
    destination: join(directory, 'backup'), maximum: 1000000, bounds } };
}

export async function legacyArchive(options, sql, encrypt = encryptArchive) {
  const directory = options.destination;
  await mkdir(directory, { mode: 0o700 });
  const configBytes = await readFile(options.config), secretsBytes = await readFile(options.secrets);
  const input = await validateInputs(configBytes, secretsBytes, options);
  const validation = await restoreAndValidate(sql, join(directory, 'validation.sqlite'), options.bounds);
  await rm(join(directory, 'validation.sqlite'));
  const state = { version: 1, jobId: crypto.randomUUID(), state: 'complete', createdAt: Date.now(),
    bookmark: 'offline-legacy-fixture', sqlDigest: digest(sql), cost: { sqlBytes: sql.length },
    scope: { account: options.account, database: options.database, inputDigest: input.inputDigest, bounds: options.bounds } };
  const manifest = { format: 'community-d1-backup-1', jobId: state.jobId, createdAt: state.createdAt,
    inputDigest: input.inputDigest, account: options.account, database: options.database, snapshotBookmark: state.bookmark,
    consistency: 'provider_full_export_query_lock', configurationSource: 'operator_supplied_not_provider_attested',
    configuration: input.config, validation, sql: { name: 'database.sql', bytes: sql.length, sha256: state.sqlDigest },
    secretInventory: secretNames, authorization: 'restore_disabled_no_automatic_activation', bounds: options.bounds };
  const members = { 'database.sql': sql, 'manifest.json': JSON.stringify(manifest), 'secrets.json': secretsBytes,
    'secret-manifest.json': JSON.stringify({ format: 'community-d1-secrets-1', jobId: state.jobId,
      inputDigest: input.inputDigest, inventory: secretNames, configurationSha256: digest(Buffer.from(JSON.stringify(input.config))) }) };
  for (const [name, bytes] of Object.entries(members)) await writeFile(join(directory, name), bytes, { mode: 0o600 });
  for (const [name, files] of [['snapshot.tar.age', ['manifest.json', 'database.sql']], ['secrets.tar.age', ['secret-manifest.json', 'secrets.json']]]) {
    await encrypt(directory, files, options.recipient, name);
  }
  state.files = await Promise.all(['snapshot.tar.age', 'secrets.tar.age'].map(async name => ({
    name, bytes: (await stat(join(directory, name))).size, sha256: digest(await readFile(join(directory, name))),
  })));
  await writeFile(join(directory, 'state.json'), JSON.stringify(state), { mode: 0o600 });
  for (const name of Object.keys(members)) await rm(join(directory, name));
}