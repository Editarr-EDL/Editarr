import { env } from './environment';
import { expect } from 'vitest';
import payloads from '../../migrations/0010_d1_artifacts.sql?raw';
import catalog from '../../migrations/0009_catalog.sql?raw';
import downloadUsage from '../../migrations/0011_download_usage.sql?raw';
import importReceipts from '../../migrations/0012_import_receipts.sql?raw';
import votes from '../../migrations/0013_votes.sql?raw';
import moderation from '../../migrations/0014_moderation.sql?raw';
import { verifyOAuthUpgrade } from './discord-oauth-upgrade';

export async function verifyCatalogUpgrade(version: number) {
  const db = env.CATALOG_DB;
  await verifyOAuthUpgrade(Math.min(version, 7));
  if (version === 8) await db.prepare(`INSERT INTO profile_name_changes VALUES ('upgrade-contributor', 'preserved-name-change', 500)`).run();
  const tables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(catalog.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare(`SELECT count(*) AS total FROM contribution_revisions`).first()).toEqual({ total: 0 });
  await db.prepare(`INSERT INTO works VALUES ('upgrade-work', 'movie', 1)`).run();
  await db.prepare(`INSERT INTO work_identifiers VALUES ('tmdb', 'movie', '123', 'upgrade-work', 'user_asserted', 'initial')`).run();
  expect(await db.prepare(`SELECT work_id FROM work_identifiers WHERE provider = 'tmdb' AND entity_type = 'movie' AND value = '123'`).first()).toEqual({ work_id: 'upgrade-work' });
  await verifyPayloadUpgrade();
}
/** Snapshot every existing application table before the additive SQLite artifact migration. */
export async function verifyPayloadUpgrade() {
  const db = env.CATALOG_DB;
  const tables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(payloads.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first()).toEqual({ total: 0 });
  const payloadTables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const payloadSnapshots = new Map<string, unknown[]>();
  for (const table of payloadTables) payloadSnapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(downloadUsage.replace(/\s*\n\s*/g, ' '));
  for (const table of payloadTables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(payloadSnapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare('SELECT count(*) AS total FROM download_usage_observations').first()).toEqual({ total: 0 });
  await verifyImportUpgrade();
}

export async function verifyImportUpgrade() {
  const db = env.CATALOG_DB;
  const tables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(importReceipts.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare('SELECT count(*) AS total FROM import_receipts').first()).toEqual({ total: 0 });
  await verifyVoteUpgrade();
}

export async function verifyVoteUpgrade() {
  const db = env.CATALOG_DB;
  const tables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(votes.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare('SELECT count(*) AS total FROM revision_votes').first()).toEqual({ total: 0 });
  await verifyModerationUpgrade();
}

export async function verifyModerationUpgrade() {
  const db = env.CATALOG_DB;
  const tables = (await db.prepare(`SELECT name FROM sqlite_schema WHERE type = 'table'
    AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*' ORDER BY name`).all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(moderation.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  expect(await db.prepare('SELECT epoch FROM moderation_epoch').first()).toEqual({ epoch: 0 });
}
