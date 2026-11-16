import { expect, it } from 'vitest';
import imports from '../migrations/0012_import_receipts.sql?raw';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { verifyVoteUpgrade } from './helpers/catalog-upgrade';

it('preserves populated 0012 imports, operations, counters, exact signed BLOBs and observations', async () => {
  await setupPublication();
  await db.exec(imports.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  const published = await publishArtifact(db, item.identity, item.input, limits, now);
  const result = published.result;
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('upgrade-reader', 'discord-reader', 'Reader', 'active', 1)"),
    db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
      .bind('upgrade-reader', result.revisionId, 'd'.repeat(64), result.artifactDigest, now),
    db.prepare("INSERT INTO operations VALUES ('upgrade-reader', 'receipt', 'saved-import', 'original-receipt', 'completed', '{}', 1, 1)"),
    db.prepare("INSERT INTO import_usage_observations VALUES ('upgrade-reader', '2026-10-04', 'allowed', 2, 10, 1, 15, 1, 1)"),
  ]);
  await verifyVoteUpgrade();
  expect(await db.prepare('SELECT imports, likes, dislikes FROM contribution_statistics WHERE revision_id = ?')
    .bind(result.revisionId).first()).toEqual({ imports: 1, likes: 0, dislikes: 0 });
  expect(await db.prepare('SELECT count(*) AS total FROM import_receipts').first()).toEqual({ total: 1 });
  expect(await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first()).toEqual({ total: 1 });
});