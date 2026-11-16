import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, rm, readdir, writeFile, mkdtemp, chmod, stat, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { restoreJob } from '../../scripts/backup/restore.mjs';
import { run } from '../../scripts/operator-cli.mjs';
import { decryptArchive } from '../../scripts/backup/archive.mjs';
import { encryptArchive } from '../../scripts/backup/files.mjs';
import { digest } from '../../scripts/backup/validate.mjs';
import { rankRevision, readPublication, lookupIdentifiers } from '../../scripts/backup/artifact-runtime.mjs';
import { signedFixture, dump, jobInputs, legacyArchive, bounds, token } from './fixtures/backup.mjs';

const optionsFor = (input, name = 'restored') => ({ source: input.options.destination, destination: join(input.directory, name),
  identity: input.identity, maximum: 1000000, archiveMaximum: 1100000, bounds, confirm: true });

async function rewritePair(input, change) {
  const source = input.options.destination;
  const snapshot = await decryptArchive(join(source, 'snapshot.tar.age'), input.identity,
    { 'manifest.json': 262144, 'database.sql': 1000000 }, 1100000);
  const secrets = await decryptArchive(join(source, 'secrets.tar.age'), input.identity,
    { 'secret-manifest.json': 131072, 'secrets.json': 131072 }, 1100000);
  const state = JSON.parse(await readFile(join(source, 'state.json')));
  const manifest = JSON.parse(snapshot.get('manifest.json'));
  const secretManifest = JSON.parse(secrets.get('secret-manifest.json'));
  change(snapshot, manifest, secretManifest);
  const sql = snapshot.get('database.sql');
  manifest.sql = { name: 'database.sql', bytes: sql.length, sha256: digest(sql) };
  state.sqlDigest = digest(sql); state.cost.sqlBytes = sql.length;
  snapshot.set('manifest.json', Buffer.from(JSON.stringify(manifest)));
  secrets.set('secret-manifest.json', Buffer.from(JSON.stringify(secretManifest)));
  const staging = await mkdtemp(join(input.directory, 'repack-'));
  try {
    for (const [name, bytes] of [...snapshot, ...secrets]) await writeFile(join(staging, name), bytes, { mode: 0o600 });
    for (const [name, members] of [['snapshot.tar.age', snapshot], ['secrets.tar.age', secrets]]) {
      await encryptArchive(staging, [...members.keys()], input.options.recipient, name);
      const bytes = await readFile(join(staging, name));
      await writeFile(join(source, name), bytes);
      Object.assign(state.files.find(file => file.name === name), { bytes: bytes.length, sha256: digest(bytes) });
    }
    await writeFile(join(source, 'state.json'), JSON.stringify(state));
  } finally { await rm(staging, { recursive: true, force: true }); }
}

test('restore dry-run is network-free and never enables an environment', async () => {
  const result = await restoreJob({}, { execute: () => assert.fail('dry-run') });
  assert.equal(result.dryRun, true); assert.equal(result.enabled, false);
  assert.equal((await run(['restore'], { token: () => assert.fail('no Access'), fetcher: () => assert.fail('no network') })).dryRun, true);
  await assert.rejects(run(['restore', '--origin', 'https://operator.example.test']), /invalid_arguments/);
  await assert.rejects(run(['restore', '--token-file', '/tmp/unused']), /invalid_arguments/);
  await assert.rejects(run(['backup', '--confirm'], { token: () => assert.fail('no credentials'),
    fetcher: () => assert.fail('no provider network') }), /container_maintenance_required/);
  assert.match((await run(['--help'])).help, /Docker Compose's offline maintenance backup/);
});

test('real encrypted archive pair restores exact signed bytes into a disabled private generation', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db, bytes } = await signedFixture(undefined, database => database.exec(
    "INSERT INTO work_identifiers(work_id,provider,entity_type,value,provenance,evidence_ref) VALUES ('work-1','tmdb','movie','123','verified_source','synthetic'); INSERT INTO release_work_associations(release_id,work_id) VALUES ('release-1','work-1')"));
  try {
    db.exec("UPDATE contribution_statistics SET imports=12,likes=8 WHERE revision_id='submission-ai'");
    await legacyArchive(input.options, dump(db));
    const options = optionsFor(input);
    const started = Date.now();
    const result = await restoreJob(options, { execute: async (command, args, settings) => {
      assert.deepEqual(Object.keys(settings.env), ['PATH']);
      assert.equal(args[1], await realpath(options.destination));
      assert.ok(!args.join(' ').includes(input.options.database));
      return promisify(execFile)(command, args, settings);
    } });
    context.diagnostic(JSON.stringify({ source: 'synthetic_local_restore_not_hosted_billing',
      restoreWallMs: Date.now() - started, sqlBytes: dump(db).length, artifacts: result.artifacts,
      payloadBytes: result.payloadBytes, sqliteBytes: (await stat(join(options.destination, 'restore.sqlite'))).size,
      providerD1Reads: null, providerD1Writes: null }));
    assert.equal(result.enabled, false); assert.equal(result.countersRebuilt, true);
    assert.equal(result.artifacts, 1); assert.equal(result.providerUsage, null);
    assert.deepEqual((await readdir(options.destination)).sort(),
      ['configuration.json', 'restore-report.json', 'restore.sqlite', 'secrets.json', 'state.json']);
    const restored = new DatabaseSync(join(options.destination, 'restore.sqlite'), { readOnly: true });
    try {
      assert.deepEqual(Buffer.from(restored.prepare('SELECT artifact FROM artifact_payloads').get().artifact), bytes.artifact);
      assert.equal(restored.prepare('SELECT state FROM installation_keys').get().state, 'retired');
      assert.equal(restored.prepare('SELECT imports FROM contribution_statistics').get().imports, 0);
      assert.equal(restored.prepare('PRAGMA foreign_key_check').all().length, 0);
      const adapter = { prepare: sql => ({ bind: (...values) => ({
        first: async () => restored.prepare(sql).get(...values) ?? null,
        all: async () => ({ results: restored.prepare(sql).all(...values) }),
      }) }) };
      const parts = await readPublication(adapter, 'object-1');
      for (const name of ['artifact', 'manifest', 'envelope']) assert.deepEqual(Buffer.from(parts[name].bytes), bytes[name]);
      const matches = await lookupIdentifiers(adapter, [{ provider: 'tmdb', entity_type: 'movie', value: '123', provenance: 'signed_claim' }],
        { maxKeys: 1, maxResults: 1, maxQueries: 1 });
      assert.equal(matches[0].revision_id, 'submission-ai');
      assert.equal(rankRevision(0, 0).qualityScore, 0.094528655);
    } finally { restored.close(); }
    const configuration = JSON.parse(await readFile(join(options.destination, 'configuration.json')));
    assert.deepEqual(configuration.bindings, {}); assert.equal(configuration.activationAllowed, false);
    assert.equal(configuration.vars.APP_ENV, 'restore-disabled');
    const report = await readFile(join(options.destination, 'restore-report.json'), 'utf8');
    assert.ok(!report.includes(token)); assert.ok(!report.includes('synthetic-bot-secret'));
    assert.equal(JSON.parse(report).providerFreshness, 'unknown');
    assert.deepEqual(await restoreJob({ ...options, resume: true }), result);
  } finally { db.close(); }
});

test('interrupted import resumes identical source; corrupt completion, changed limits and incomplete source fail closed', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  try {
    await legacyArchive(input.options, dump(db));
    const options = optionsFor(input);
    await assert.rejects(restoreJob(options, { execute: async () => { throw new Error('interrupted'); } }), /restore_import_interrupted/);
    const state = JSON.parse(await readFile(join(options.destination, 'state.json')));
    assert.equal(state.state, 'importing'); assert.equal(state.lastError, 'restore_import_interrupted');
    assert.ok(!(await readdir(options.destination)).includes('configuration.json'));
    await assert.rejects(restoreJob({ ...options, resume: true, maximum: 999999 }), /restore_checkpoint_scope_mismatch/);
    const result = await run(['restore', '--source', options.source, '--destination', options.destination,
      '--identity-file', input.identity, '--maximum-archive-bytes', '1100000', '--maximum-sql-bytes', '1000000',
      '--artifact-bounds', JSON.stringify(bounds), '--confirm', '--resume']);
    assert.equal(result.state, 'complete'); assert.equal(result.activationAllowed, false);
    const complete = JSON.parse(await readFile(join(options.destination, 'state.json')));
    complete.state = 'validated'; await writeFile(join(options.destination, 'state.json'), JSON.stringify(complete));
    await writeFile(join(options.destination, 'database.sql'), 'private staging', { mode: 0o600 });
    assert.equal((await restoreJob({ ...options, resume: true })).state, 'complete');
    assert.ok(!(await readdir(options.destination)).includes('database.sql'));
    await writeFile(join(options.destination, 'restore.sqlite'), 'truncated');
    await assert.rejects(restoreJob({ ...options, resume: true }), /completed_restore_corrupt/);
    await assert.rejects(restoreJob({ ...options, destination: options.source }), /restore_destination_is_source/);
    const sourceState = JSON.parse(await readFile(join(options.source, 'state.json')));
    sourceState.state = 'exporting'; await writeFile(join(options.source, 'state.json'), JSON.stringify(sourceState));
    await assert.rejects(restoreJob(optionsFor(input, 'incomplete')), /restore_complete_backup_required/);
  } finally { db.close(); }
});

test('protected identity and ciphertext are required, with no source generation silently substituted', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  try {
    await legacyArchive(input.options, dump(db));
    await chmod(input.identity, 0o644);
    await assert.rejects(restoreJob(optionsFor(input)), /unsafe_backup_input/);
    await chmod(input.identity, 0o600);
    assert.equal((await restoreJob({ ...optionsFor(input), resume: true })).enabled, false);
    for (const name of await readdir(optionsFor(input).destination))
      assert.equal((await stat(join(optionsFor(input).destination, name))).mode & 0o077, 0);
    await writeFile(join(input.options.destination, 'snapshot.tar.age'), 'corrupt');
    await assert.rejects(restoreJob(optionsFor(input, 'corrupt-source')), /restore_source_ciphertext_mismatch/);
  } finally { db.close(); }
});

test('snapshot manifest includes configuration overhead without exceeding its independent bound', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  try {
    const configuration = JSON.parse(await readFile(input.options.config));
    configuration.vars.PUBLICATION_LIMITS = 'x'.repeat(65536);
    configuration.vars.LOOKUP_LIMITS = 'x'.repeat(63000);
    await writeFile(input.options.config, JSON.stringify(configuration));
    assert.ok((await stat(input.options.config)).size < 131072);
    await legacyArchive(input.options, dump(db));
    const snapshot = await decryptArchive(join(input.options.destination, 'snapshot.tar.age'), input.identity,
      { 'manifest.json': 262144, 'database.sql': 1000000 }, 1100000);
    assert.ok(snapshot.get('manifest.json').length > 131072);
    assert.equal((await restoreJob(optionsFor(input))).state, 'complete');
  } finally { db.close(); }
});

test('mixed pair, unknown schema, corrupt payload, stale storage and interrupted SQL never complete', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db, bytes } = await signedFixture();
  try {
    for (const [index, mutation, error] of [
      [0, (snapshot, manifest, secret) => { secret.jobId = crypto.randomUUID(); }, /restore_archive_pair_mismatch/],
      [1, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nCREATE TABLE unknown_schema(x);')])), /unsupported_backup_schema/],
      [2, snapshot => snapshot.set('database.sql', Buffer.from(snapshot.get('database.sql').toString().replace(
        bytes.artifact.toString('hex').toUpperCase(), bytes.artifact.subarray(0, -1).toString('hex').toUpperCase()))), /backup_payload_mismatch/],
      [3, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nUPDATE artifact_storage_usage SET bytes=bytes+1;')])), /backup_storage_counter_mismatch/],
      [4, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nCREATE TABLE broken(')])), /backup_sql_restore_failed/],
      [5, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nDELETE FROM operations;')])), /backup_integrity_failed/],
      [6, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nUPDATE installation_keys SET public_key=zeroblob(32);')])), /backup_signature_invalid/],
      [7, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'), Buffer.from('\nDELETE FROM d1_migrations WHERE id=14;')])), /unknown_migration_revision/],
    ]) {
      const sourceOptions = { ...input.options, destination: join(input.directory, `backup-${index}`) };
      await legacyArchive(sourceOptions, dump(db));
      const altered = { ...input, options: sourceOptions };
      await rewritePair(altered, mutation);
      const options = optionsFor(altered, `refused-${index}`);
      await assert.rejects(restoreJob(options), error);
      assert.notEqual(JSON.parse(await readFile(join(options.destination, 'state.json'))).state, 'complete');
      assert.ok(!(await readdir(options.destination)).includes('configuration.json'));
    }
  } finally { db.close(); }
});

test('untrusted snapshot SQL cannot attach or overwrite another database', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  const sentinel = join(input.directory, 'untouched.sqlite');
  const target = new DatabaseSync(sentinel);
  target.exec("CREATE TABLE sentinel(value TEXT); INSERT INTO sentinel VALUES ('preserved')"); target.close();
  const original = digest(await readFile(sentinel));
  try {
    await legacyArchive(input.options, dump(db));
    await rewritePair(input, snapshot => snapshot.set('database.sql', Buffer.concat([snapshot.get('database.sql'),
      Buffer.from(`\nATTACH '${sentinel.replaceAll("'", "''")}' AS production; DELETE FROM production.sentinel;`)])));
    await assert.rejects(restoreJob(optionsFor(input)), /backup_sql_restore_failed/);
    assert.equal(digest(await readFile(sentinel)), original);
  } finally { db.close(); }
});

test('legacy macOS tar output from CSS-60 remains readable without metadata extraction', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  try {
    const execute = promisify(execFile);
    await legacyArchive(input.options, dump(db), async (directory, members, recipient, name) => {
      const tar = join(input.directory, `${name}.tar`);
      await execute('tar', ['-cf', tar, '-C', directory, ...members]);
      await execute('age', ['-r', recipient, '-o', join(directory, name), tar]);
      await chmod(join(directory, name), 0o600);
    });
    assert.equal((await restoreJob(optionsFor(input))).state, 'complete');
    assert.ok(!(await readdir(optionsFor(input).destination)).some(name => name.startsWith('._') || name.startsWith('PaxHeader')));
  } finally { db.close(); }
});

test('populated restore retains neutral votes, exclusions, suspension, revoked keys, audit and historical usage', async context => {
  const input = await jobInputs(); context.after(() => rm(input.directory, { recursive: true, force: true }));
  const { db } = await signedFixture();
  try {
    const authorKey = db.prepare('SELECT public_key FROM installation_keys').get().public_key;
    const artifactDigest = db.prepare('SELECT artifact_digest FROM contribution_revisions').get().artifact_digest;
    for (const value of ['like', 'dislike', 'neutral']) {
      const contributor = `voter-${value}`, key = `key-${value}`;
      db.prepare('INSERT INTO contributors(contributor_id,discord_user_id,display_name,created_at,display_name_key) VALUES (?,?,?,?,?)')
        .run(contributor, `discord-${value}`, contributor, 1, contributor);
      db.prepare('INSERT INTO installations VALUES (?,?,1)').run(`install-${value}`, contributor);
      db.prepare("INSERT INTO installation_keys VALUES (?,?,'Ed25519',?,'active',1,NULL)").run(key, `install-${value}`, authorKey);
      db.prepare("INSERT INTO import_receipts VALUES (?,'submission-ai',?,?,1,100)").run(contributor, digest(Buffer.from(value)), artifactDigest);
      db.prepare("INSERT INTO revision_votes VALUES (?,'submission-ai',?,?,101)").run(contributor, value, key);
      db.prepare("INSERT INTO operations VALUES (?,'vote',?,'digest','completed',?,101,101)")
        .run(contributor, `vote-${value}`, JSON.stringify({ version: 1, operationId: `vote-${value}`, revisionId: 'submission-ai', vote: value }));
    }
    db.exec("INSERT INTO reports VALUES ('report-1','voter-neutral','submission-ai','key-neutral','privacy','private synthetic note','open',102,10,10)");
    const moderate = (command, resource, target, revision = null) => {
      const expected = db.prepare('SELECT revision FROM moderation_versions WHERE resource=?').get(resource)?.revision ?? 0;
      db.prepare('INSERT INTO moderation_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)').run(crypto.randomUUID(), 'synthetic-human',
        resource, target, revision, command, expected, 'Synthetic review', JSON.stringify({ command, target, revision }), 'applied', 103);
    };
    moderate('exclude_vote', 'vote:voter-dislike:submission-ai', 'voter-dislike', 'submission-ai');
    moderate('suspend', 'contributor:voter-like', 'voter-like');
    moderate('quarantine', 'revision:submission-ai', 'submission-ai');
    db.exec("UPDATE installation_keys SET state='revoked' WHERE key_id='key-1'; UPDATE contribution_statistics SET imports=99,likes=99,dislikes=99; INSERT INTO vote_usage_observations VALUES ('voter-like','2026-10-03','allowed',1,5,1,2,1,123)");
    const epoch = db.prepare('SELECT epoch FROM moderation_epoch').get().epoch;
    await legacyArchive(input.options, dump(db));
    const options = optionsFor(input);
    const result = await restoreJob(options);
    assert.equal(result.countersRebuilt, true);
    const restored = new DatabaseSync(join(options.destination, 'restore.sqlite'), { readOnly: true });
    try {
      const counts = restored.prepare('SELECT imports,likes,dislikes FROM contribution_statistics').get();
      assert.deepEqual({ ...counts }, { imports: 3, likes: 1, dislikes: 0 });
      assert.equal(rankRevision(counts.likes, counts.dislikes).qualityScore, 0.207654955);
      assert.equal(restored.prepare("SELECT value FROM revision_votes WHERE contributor_id='voter-neutral'").get().value, 'neutral');
      assert.equal(restored.prepare("SELECT state FROM contributors WHERE contributor_id='voter-like'").get().state, 'suspended');
      assert.equal(restored.prepare("SELECT state FROM installation_keys WHERE key_id='key-1'").get().state, 'revoked');
      assert.equal(restored.prepare('SELECT state FROM contribution_publications').get().state, 'quarantined');
      assert.equal(restored.prepare('SELECT excluded FROM vote_exclusions').get().excluded, 1);
      assert.equal(restored.prepare('SELECT count(*) AS total FROM moderation_actions').get().total, 3);
      assert.equal(restored.prepare('SELECT epoch FROM moderation_epoch').get().epoch, epoch);
      assert.equal(restored.prepare('SELECT note FROM reports').get().note, 'private synthetic note');
      assert.equal(restored.prepare("SELECT count(*) AS total FROM operations WHERE kind='vote'").get().total, 3);
      assert.equal(restored.prepare('SELECT unknown_reads FROM vote_usage_observations').get().unknown_reads, 1);
    } finally { restored.close(); }
    const report = JSON.parse(await readFile(join(options.destination, 'restore-report.json')));
    assert.deepEqual(report.usage.find(row => row.table === 'vote_usage_observations').period,
      { first: '2026-10-03', last: '2026-10-03' });
    assert.equal(report.providerUsage, null); assert.equal(report.providerFreshness, 'unknown');
    assert.ok(!JSON.stringify(report).includes('private synthetic note'));
  } finally { db.close(); }
});