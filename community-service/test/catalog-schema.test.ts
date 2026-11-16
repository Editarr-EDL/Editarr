import { env } from './helpers/environment';
import { beforeAll, expect, it } from 'vitest';
import identity from '../migrations/0001_identity_operations.sql?raw';
import catalog from '../migrations/0009_catalog.sql?raw';
import { addVerifiedIdentifier, ensureWork } from '../src/catalog/works';

const db = env.CATALOG_DB;
beforeAll(async () => {
  await db.exec(identity.replace(/\s*\n\s*/g, ' '));
  await db.exec(catalog.replace(/\s*\n\s*/g, ' '));
});

it('resolves concurrent typed identifiers without merging user-asserted aliases', async () => {
  const input = { provider: 'tmdb', entity_type: 'movie', value: '789', provenance: 'user_confirmed' } as const;
  const [first, second] = await Promise.all([ensureWork(db, input, 2), ensureWork(db, input, 2)]);
  expect(first).toBe(second);
  const alias = { ...input, provider: 'tvdb', value: '789' } as const;
  await addVerifiedIdentifier(db, first, alias, { provenance: 'operator', evidenceRef: 'review-1' });
  expect(await ensureWork(db, alias, 3)).toBe(first);
  await expect(addVerifiedIdentifier(db, 'work', alias, { provenance: 'operator', evidenceRef: 'review-2' })).rejects.toThrow('work_identifier_conflict');
  await expect(db.prepare(`DELETE FROM work_identifiers WHERE value = '789'`).run()).rejects.toThrow('immutable_work_identifier');
});

it('accepts the documented 100-bind envelope and records local 101-bind behavior', async () => {
  expect(await db.prepare(`SELECT ${Array(100).fill('?').join('+')} AS total`).bind(...Array(100).fill(1)).first()).toEqual({ total: 100 });
  let local101 = 'accepted';
  try { await db.prepare(`SELECT ${Array(101).fill('?').join('+')} AS total`).bind(...Array(101).fill(1)).first(); }
  catch { local101 = 'rejected'; }
  console.info(`SQLite local bind probe: 100 accepted, 101 ${local101}; production query builders remain <=100`);
});

it('adds catalog indexes without changing existing identity data', async () => {
  await db.prepare(`INSERT INTO contributors VALUES ('author', 'discord', 'Name', 'active', 1)`).run();
  await db.prepare(`INSERT INTO works VALUES ('work', 'movie', 1)`).run();
  await db.prepare(`INSERT INTO work_identifiers VALUES ('tmdb', 'movie', '123', 'work', 'user_asserted', 'initial')`).run();
  await expect(db.prepare(`INSERT INTO work_identifiers VALUES ('tvdb', 'movie', '456', 'work', 'user_asserted', 'claim')`).run()).rejects.toThrow('unverified_work_alias');
  await expect(db.prepare(`INSERT INTO work_identifiers VALUES ('tmdb', 'episode', '1', 'work', 'operator', 'proof')`).run()).rejects.toThrow();
  await expect(db.prepare(`UPDATE work_identifiers SET work_id = 'other'`).run()).rejects.toThrow('immutable_work_identifier');
  expect(await db.prepare(`SELECT display_name FROM contributors WHERE contributor_id = 'author'`).first()).toEqual({ display_name: 'Name' });
  const plan = await db.prepare(`EXPLAIN QUERY PLAN SELECT release_id FROM release_fingerprints WHERE channel = ? AND algorithm = ? AND digest = ?`).bind('audio', 'test', 'a').all<{ detail: string }>();
  expect(plan.results.some(item => item.detail.includes('idx_release_fingerprints_lookup'))).toBe(true);
});