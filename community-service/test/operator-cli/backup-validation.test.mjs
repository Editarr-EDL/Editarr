import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { knownSchema, validateDatabase, restoreAndValidate } from '../../scripts/backup/validate.mjs';
import { emptyFixture, signedFixture, dump, bounds } from './fixtures/backup.mjs';
import { reconcileCounters } from '../../scripts/backup/counters.mjs';

test('isolated legacy validation inventories the original application tables and preserves schema revision', async () => {
  const db = await emptyFixture();
  try {
    const result = await validateDatabase(db, bounds);
    assert.equal(result.schemaRevision, '0014_moderation.sql');
    assert.equal((await knownSchema()).files.length, 14);
    assert.equal(result.artifacts, 0);
    assert.equal(result.providerUsage, null);
    for (const table of ['operations', 'artifact_payloads', 'installation_keys', 'moderation_actions', 'revision_votes', 'import_receipts'])
      assert.ok(result.inventory.find(item => item.table === table));
    await validateDatabase(db, bounds, result.inventory);
    db.prepare("INSERT INTO contributors(contributor_id,discord_user_id,display_name,created_at,display_name_key) VALUES ('c','d','name',0,'name')").run();
    await assert.rejects(validateDatabase(db, bounds, result.inventory), /backup_inventory_mismatch/);
  } finally { db.close(); }
});

test('unknown or missing schema, migration records and stale payload counters are refused', async () => {
  for (const sql of ['DROP TABLE reports', 'DROP TABLE operations', 'DROP TABLE d1_migrations', 'DELETE FROM d1_migrations WHERE id=14',
    "INSERT INTO artifact_storage_usage VALUES ('global',1)"]) {
    const db = await emptyFixture();
    try { db.exec(sql); await assert.rejects(validateDatabase(db, bounds)); }
    finally { db.close(); }
  }
  const db = await emptyFixture();
  try {
    const known = await knownSchema();
    db.exec('DROP TABLE d1_migrations');
    db.exec(`CREATE VIEW d1_migrations AS ${known.files.map(name => `SELECT '${name}' AS name`).join(' UNION ALL ')}`);
    await assert.rejects(validateDatabase(db, bounds), /migration_inventory_missing/);
  } finally { db.close(); }
});

test('isolated restore denies attached files, extension loading, unsafe pragmas and truncated SQL', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'css60-validation-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  for (const [index, sql] of ["ATTACH DATABASE '/tmp/css60-should-not-exist' AS extra", "SELECT load_extension('evil')",
    'PRAGMA writable_schema=ON', 'CREATE TABLE broken('].entries()) {
    await assert.rejects(restoreAndValidate(Buffer.from(sql), join(directory, `restore-${index}.sqlite`), bounds),
      /backup_sql_restore_failed/);
  }
});

test('signed BLOBs and retired author keys survive isolated full SQL restore byte-for-byte', async context => {
  const { db, bytes } = await signedFixture();
  const directory = await mkdtemp(join(tmpdir(), 'css60-signed-'));
  context.after(() => rm(directory, { recursive: true, force: true }));
  try {
    const original = await validateDatabase(db, bounds);
    assert.equal(original.artifacts, 1);
    assert.equal(original.payloadBytes, bytes.artifact.length + bytes.manifest.length + bytes.envelope.length);
    const restored = await restoreAndValidate(dump(db), join(directory, 'restore.sqlite'), bounds, original.inventory);
    assert.ok(restored.sqliteBytes > 0);
    assert.deepEqual({ ...restored, sqliteBytes: null }, { ...original, sqliteBytes: null });
  } finally { db.close(); }
});

test('corrupt/truncated BLOBs, missing payloads, altered keys and orphan completed operations are refused', async () => {
  for (const [trigger, mutation, error] of [
    ['guard_payload_update', "UPDATE artifact_payloads SET artifact=substr(artifact,1,length(artifact)-1)", /backup_payload_mismatch/],
    ['guard_payload_update', "UPDATE artifact_payloads SET manifest_digest='wrong'", /backup_payload_mismatch/],
    ['guard_payload_delete', 'DELETE FROM artifact_payloads', /backup_payload_mismatch/],
    [null, "UPDATE installation_keys SET public_key=zeroblob(32)", /backup_signature_invalid/],
    [null, "INSERT INTO operations VALUES ('contributor-1','publication','orphan','digest','completed','{}',1,1)", /backup_operation_link_invalid/],
    [null, 'DELETE FROM artifact_storage_policy', /backup_storage_policy_invalid/],
  ]) {
    const { db } = await signedFixture();
    try {
      const original = trigger ? db.prepare('SELECT sql FROM sqlite_schema WHERE name=?').get(trigger).sql : null;
      if (trigger) db.exec(`DROP TRIGGER ${trigger}`);
      db.exec(mutation);
      if (original) db.exec(original);
      await assert.rejects(validateDatabase(db, bounds), error);
    } finally { db.close(); }
  }
});

test('restore reconciles derived reputation only and refuses missing durable statistics linkage', async () => {
  const { db } = await signedFixture();
  try {
    db.exec("UPDATE contribution_statistics SET imports=12,likes=8,dislikes=5 WHERE revision_id='submission-ai'");
    const original = await validateDatabase(db, bounds);
    const result = await reconcileCounters(db, bounds, original);
    assert.equal(result.changed, true);
    assert.deepEqual({ ...db.prepare('SELECT imports,likes,dislikes FROM contribution_statistics').get() },
      { imports: 0, likes: 0, dislikes: 0 });
    assert.equal(db.prepare('SELECT state FROM installation_keys').get().state, 'retired');
    assert.equal((await reconcileCounters(db, bounds, result.validation)).changed, false);
    db.exec('DELETE FROM contribution_statistics');
    await assert.rejects(reconcileCounters(db, bounds, await validateDatabase(db, bounds)), /restore_reputation_facts_invalid/);
  } finally { db.close(); }
});