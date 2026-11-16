import { beforeAll, expect, it } from 'vitest';
import migration from '../migrations/0011_download_usage.sql?raw';
import { observeDownloadDatabase, recordDownloadUsage } from '../src/budgets/download-usage';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, publicationCase, setupPublication } from './helpers/publication';
import { verifyImportUpgrade } from './helpers/catalog-upgrade';

beforeAll(setupPublication);
it('adds read observations without changing existing application rows and preserves unknown readings', async () => {
  const { identity, input } = await publicationCase('manual');
  await publishArtifact(db, identity, input, limits, now);
  const tables = (await db.prepare("SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*'").all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect(await db.prepare('PRAGMA foreign_key_check').all()).toMatchObject({ results: [] });
  const observer = observeDownloadDatabase(db, 2);
  expect(await observer.db.prepare("SELECT contributor_id FROM contributors WHERE contributor_id = 'contributor-1'").first('contributor_id')).toBe('contributor-1');
  await observer.db.prepare("SELECT * FROM contributors WHERE contributor_id = 'contributor-1'").all();
  expect(observer.snapshot().rowsRead).toBeNull();
  await expect(observer.db.prepare('SELECT 1').first()).rejects.toThrow('download_query_limit');
  await recordDownloadUsage(db, 'contributor-1', 'allowed', now, observer.snapshot());
  await recordDownloadUsage(db, 'contributor-1', 'allowed', now, { rowsRead: null, rowsWritten: null });
  expect(await db.prepare('SELECT occurrences, unknown_reads, unknown_writes FROM download_usage_observations').first())
    .toEqual({ occurrences: 2, unknown_reads: 2, unknown_writes: 1 });
  const failed = observeDownloadDatabase(db, 2);
  await expect(failed.db.prepare('SELECT nonexistent_field FROM contributors').first()).rejects.toThrow();
  expect(failed.snapshot()).toMatchObject({ rowsRead: null, rowsWritten: null });
  await verifyImportUpgrade();
});