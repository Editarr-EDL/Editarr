import { expect, it } from 'vitest';
import { env } from './helpers/environment';
import localUsage from '../migrations/0015_local_usage.sql?raw';
import localReports from '../migrations/0016_local_reports.sql?raw';
import { readFileSync, readdirSync } from 'node:fs';

it('retires Docker usage enforcement without deleting historical counters or identity', async () => {
  const db = env.CATALOG_DB;
  const directory = new URL('../migrations/', import.meta.url);
  for (const file of readdirSync(directory).filter(file => /^00(0\d|1[0-4])_.*\.sql$/.test(file)).sort()) {
    await db.exec(readFileSync(new URL(file, directory), 'utf8'));
  }
  await db.prepare(`INSERT INTO contributors
    (contributor_id, discord_user_id, display_name, display_name_key, created_at)
    VALUES ('local', 'local-discord', 'Local', 'local', 1)`).run();
  await db.prepare('UPDATE quota_policy SET enabled = 1').run();
  await db.prepare(`INSERT INTO quota_charges VALUES
    ('historical', 'local', 'worker_requests', '2026-10-07', 1800, 1)`).run();
  await db.exec(`
    INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES ('reporter', 'reporter-discord', 'Reporter', 'reporter', 1);
    INSERT INTO installations VALUES ('author-install', 'local', 1), ('reporter-install', 'reporter', 1);
    INSERT INTO installation_keys VALUES ('author-key', 'author-install', 'Ed25519', X'01', 'active', 1, NULL),
      ('reporter-key', 'reporter-install', 'Ed25519', X'02', 'active', 1, NULL);
    INSERT INTO works VALUES ('work', 'movie', 1);
    INSERT INTO releases VALUES ('release', 'work', '{}', NULL, NULL, NULL, 1);
    INSERT INTO operations VALUES ('local', 'publication', 'publication', 'digest', 'completed', '{}', 1, 1);
    INSERT INTO contributions VALUES ('contribution', 'local', 1);
  `);
  await db.prepare(`INSERT INTO contribution_revisions
    (revision_id, contribution_id, contributor_id, operation_kind, operation_id, key_id,
      release_id, kind, artifact_schema, artifact_digest, manifest_digest, envelope_digest,
      artifact_bytes, manifest_bytes, envelope_bytes, object_key, summary_json, total_events,
      audio_events, video_events, lineage_depth, parent_limit, created_at)
    VALUES ('revision', 'contribution', 'local', 'publication', 'publication', 'author-key',
      'release', 'scan', 'community-artifact-1', ?, ?, ?, 1, 1, 1, 'object', '{}', 0, 0, 0, 0, 0, 1)`)
    .bind('a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64)).run();
  await db.exec(`
    INSERT INTO contribution_publications VALUES ('revision', 'reserved', NULL, 1);
    INSERT INTO contribution_statistics VALUES ('revision', 0, 0, 0);
  `);
  await db.prepare("INSERT INTO artifact_payloads VALUES ('object', X'01', X'02', X'03', ?, ?, ?)")
    .bind('a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64)).run();
  await db.exec("UPDATE contribution_publications SET state = 'object_stored', object_etag = 'etag' WHERE revision_id = 'revision'; UPDATE contribution_publications SET state = 'published' WHERE revision_id = 'revision'");
  await db.prepare("INSERT INTO import_receipts VALUES ('reporter', 'revision', ?, ?, 1, 1)")
    .bind('d'.repeat(64), 'a'.repeat(64)).run();
  await db.exec("INSERT INTO reports VALUES ('report', 'reporter', 'revision', 'reporter-key', 'privacy', 'Preserved private note', 'open', 1, 1, 1)");
  await db.exec(localUsage);
  await db.exec(localReports);
  expect(await db.prepare('SELECT * FROM legacy_report_allowances').first())
    .toEqual({ report_id: 'report', per_day_limit: 1, max_open_limit: 1 });
  expect(await db.prepare('SELECT note, state FROM reports').first())
    .toEqual({ note: 'Preserved private note', state: 'open' });
  await expect(db.prepare("UPDATE reports SET note = 'Tampered'").run()).rejects.toThrow('immutable_report');
  await db.prepare("UPDATE reports SET state = 'resolved'").run();
  await db.exec("INSERT INTO reports VALUES ('another-report', 'reporter', 'revision', 'reporter-key', 'spam', 'Another valid report', 'open', 1)");
  expect(await db.prepare('SELECT used FROM quota_counters').first()).toEqual({ used: 1800 });
  await db.prepare('UPDATE quota_policy SET enabled = 0').run();
  await db.prepare(`INSERT INTO quota_charges VALUES
    ('after-upgrade', 'local', 'worker_requests', '2026-10-07', 1000000, 2)`).run();
  expect(await db.prepare('SELECT used FROM quota_counters').first()).toEqual({ used: 1800 });
  expect(await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'trigger'
    AND name IN ('quota_charge', 'guard_retired_quota', 'guard_retired_budget', 'charge_payload_storage')`).all())
    .toMatchObject({ results: [] });
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
});