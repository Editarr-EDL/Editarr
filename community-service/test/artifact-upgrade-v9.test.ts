import { expect, it } from 'vitest';
import catalog from '../migrations/0009_catalog.sql?raw';
import { verifyOAuthUpgrade } from './helpers/discord-oauth-upgrade';
import { verifyPayloadUpgrade } from './helpers/catalog-upgrade';
import { db, rawRevision } from './helpers/catalog';

it('preserves populated 0009 revisions, lifecycle, keys and retired accounting without inventing payloads', async () => {
  await verifyOAuthUpgrade(7);
  await db.exec(catalog.replace(/\s*\n\s*/g, ' '));
  await db.batch([
    db.prepare("INSERT INTO works VALUES ('legacy-work', 'movie', 1)"),
    db.prepare("INSERT INTO releases VALUES ('legacy-release', 'legacy-work', '{}', NULL, NULL, NULL, 1)"),
    db.prepare("INSERT INTO quota_counters VALUES ('upgrade-contributor', 'r2_a', '2026-09', 3, 1)"),
    db.prepare("INSERT INTO usage_observations VALUES ('upgrade-contributor', 'r2_a', '2026-09', 'allowed', 1, 3, 1)"),
  ]);
  await rawRevision('legacy-revision', 'legacy-release', { author: 'upgrade-contributor', key: 'upgrade-key' });
  await db.batch([
    db.prepare("UPDATE contribution_publications SET state = 'object_stored', object_etag = 'original-etag' WHERE revision_id = 'legacy-revision'"),
    db.prepare("UPDATE contribution_publications SET state = 'published' WHERE revision_id = 'legacy-revision'"),
  ]);
  await verifyPayloadUpgrade();
  expect(await db.prepare("SELECT state, object_etag FROM contribution_publications WHERE revision_id = 'legacy-revision'").first())
    .toEqual({ state: 'published', object_etag: 'original-etag' });
  expect(await db.prepare("SELECT state FROM installation_keys WHERE key_id = 'upgrade-key'").first()).toEqual({ state: 'retired' });
  await expect(db.prepare("INSERT INTO usage_observations VALUES ('upgrade-contributor', 'r2_a', '2026-10', 'allowed', 1, 1, 2)").run())
    .rejects.toThrow('retired_resource');
  await rawRevision('pending-revision', 'legacy-release', { author: 'upgrade-contributor', key: 'upgrade-key' });
  await expect(db.prepare("UPDATE contribution_publications SET state = 'object_stored', object_etag = 'fake-confirmation' WHERE revision_id = 'pending-revision'").run())
    .rejects.toThrow('artifact_payload_missing');
});
