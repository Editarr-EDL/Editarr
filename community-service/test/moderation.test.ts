import { beforeAll, expect, it } from 'vitest';
import imports from '../migrations/0012_import_receipts.sql?raw';
import votes from '../migrations/0013_votes.sql?raw';
import moderation from '../migrations/0014_moderation.sql?raw';
import { publishArtifact } from '../src/contributions/publication';
import { db, now, limits, setupPublication, publicationCase } from './helpers/publication';
import { inspectModeration, moderate } from '../src/admin/moderation';
import { readVote, setVote, reconcileVoteCounts } from '../src/contributions/votes';
import contract from '../../tests/fixtures/community/moderation-v1.json';

let revisionId: string;
const identity = () => ({ contributorId: 'voter', keyId: 'voter-key', displayName: 'Voter', nonce: crypto.randomUUID(),
  operationId: crypto.randomUUID(), contentDigest: crypto.randomUUID(), expiresAt: now + 60 });
const action = (command: string, targetId: string, expectedRevision: number, revision?: string) => ({
  version: 1, actionId: crypto.randomUUID(), command, targetId, expectedRevision, reason: 'Reviewed abuse evidence',
  ...(revision ? { revisionId: revision } : {}),
});
beforeAll(async () => {
  await setupPublication();
  for (const migration of [imports, votes]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
  const item = await publicationCase('ai');
  const published = await publishArtifact(db, item.identity, item.input, limits, now);
  revisionId = published.result.revisionId;
  await db.batch([
    db.prepare("INSERT INTO contributors VALUES ('voter', 'discord-voter', 'Voter', 'active', 1)"),
    db.prepare("INSERT INTO installations VALUES ('voter-install', 'voter', 1)"),
    db.prepare("INSERT INTO installation_keys VALUES ('voter-key', 'voter-install', 'Ed25519', ?, 'active', 1, NULL)").bind(new Uint8Array(32)),
    db.prepare('INSERT INTO import_receipts VALUES (?, ?, ?, ?, 1, ?)')
      .bind('voter', revisionId, 'a'.repeat(64), published.result.artifactDigest, now),
    db.prepare("INSERT INTO revision_votes VALUES ('voter', ?, 'like', 'voter-key', ?)").bind(revisionId, now),
  ]);
});

it('preserves every populated 0013 row, including votes, import facts and exact signed BLOBs', async () => {
  const tables = (await db.prepare("SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name NOT GLOB '_cf_*'")
    .all<{ name: string }>()).results;
  const snapshots = new Map<string, unknown[]>();
  for (const table of tables) snapshots.set(table.name, (await db.prepare(`SELECT * FROM "${table.name}"`).all()).results);
  await db.exec(moderation.replace(/\s*\n\s*/g, ' '));
  for (const table of tables) expect((await db.prepare(`SELECT * FROM "${table.name}"`).all()).results).toEqual(snapshots.get(table.name));
  expect((await db.prepare('PRAGMA foreign_key_check').all()).results).toEqual([]);
});

it('serializes concurrent stale actions and preserves exact actor-bound retries and audit outcomes', async () => {
  const first = contract.action, second = action('quarantine', revisionId, 0);
  const results = await Promise.allSettled([moderate(db, 'operator-a', first, now), moderate(db, 'operator-b', second, now)]);
  expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
  expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
  const applied = results[0].status === 'fulfilled' ? first : second;
  const actor = applied === first ? 'operator-a' : 'operator-b';
  expect(await moderate(db, actor, applied, now)).toMatchObject({ revision: 1, outcome: 'applied' });
  if (applied === first) expect(await moderate(db, actor, applied, now)).toEqual(contract.applied);
  await expect(moderate(db, 'other-operator', applied, now)).rejects.toThrow('action_conflict');
  await expect(moderate(db, actor, { ...applied, reason: 'Changed intent' }, now)).rejects.toThrow('action_conflict');
  const inspected = await inspectModeration(db, 'revision', revisionId);
  expect(inspected).toMatchObject({ revision: 1, state: { state: 'quarantined' } });
  expect(inspected.audit.map(row => row.outcome).sort()).toEqual(['applied', 'conflict']);
  await expect(readVote(db, identity(), revisionId, false, now)).rejects.toThrow('revision_not_available');
  await moderate(db, actor, action('restore', revisionId, 1), now);
  expect(await readVote(db, identity(), revisionId, false, now)).toMatchObject({ likes: 1 });
});

it('suspends authors without modifying their keys, immutable bytes, import facts or vote totals', async () => {
  const originalPayloads = (await db.prepare('SELECT * FROM artifact_payloads').all()).results;
  const originalRevisions = (await db.prepare('SELECT * FROM contribution_revisions').all()).results;
  const originalParents = (await db.prepare('SELECT * FROM contribution_parents').all()).results;
  await moderate(db, 'operator-a', action('suspend', 'contributor-1', 0), now);
  await expect(readVote(db, identity(), revisionId, false, now)).rejects.toThrow('revision_not_available');
  expect(await db.prepare("SELECT state FROM installation_keys WHERE key_id = 'key-1'").first()).toEqual({ state: 'active' });
  expect(await db.prepare('SELECT likes, imports FROM contribution_statistics WHERE revision_id = ?').bind(revisionId).first())
    .toEqual({ likes: 1, imports: 1 });
  expect(await db.prepare('SELECT count(*) AS total FROM artifact_payloads').first()).toEqual({ total: 1 });
  await moderate(db, 'operator-a', action('quarantine', revisionId, 2), now);
  await expect(moderate(db, 'operator-a', action('restore', revisionId, 3), now)).rejects.toThrow('moderation_ineligible');
  await moderate(db, 'operator-a', action('unsuspend', 'contributor-1', 1), now);
  await expect(readVote(db, identity(), revisionId, false, now)).rejects.toThrow('revision_not_available');
  await moderate(db, 'operator-a', action('restore', revisionId, 3), now);
  expect((await db.prepare('SELECT * FROM artifact_payloads').all()).results).toEqual(originalPayloads);
  expect((await db.prepare('SELECT * FROM contribution_revisions').all()).results).toEqual(originalRevisions);
  expect((await db.prepare('SELECT * FROM contribution_parents').all()).results).toEqual(originalParents);
});

it('excludes a vote without deleting it; contributor updates cannot clear the exclusion', async () => {
  await moderate(db, 'operator-a', action('exclude_vote', 'voter', 0, revisionId), now);
  expect(await readVote(db, identity(), revisionId, false, now)).toMatchObject({ likes: 0, dislikes: 0 });
  expect(await readVote(db, identity(), revisionId, true, now)).toMatchObject({ vote: 'like' });
  await setVote(db, identity(), revisionId, { version: 1, vote: 'dislike' }, now);
  await reconcileVoteCounts(db, [revisionId]);
  expect(await readVote(db, identity(), revisionId, false, now)).toMatchObject({ likes: 0, dislikes: 0 });
  expect(await inspectModeration(db, 'vote', 'voter', revisionId)).toMatchObject({ revision: 2, state: { value: 'dislike', excluded: 1 } });
  await expect(moderate(db, 'operator-a', action('restore_vote', 'voter', 1, revisionId), now)).rejects.toThrow('moderation_conflict');
  await moderate(db, 'operator-a', action('restore_vote', 'voter', 2, revisionId), now);
  expect(await readVote(db, identity(), revisionId, false, now)).toMatchObject({ likes: 0, dislikes: 1 });
});

it('never restores votes through a suspended voter or quarantined revision', async () => {
  await moderate(db, 'operator-a', action('exclude_vote', 'voter', 3, revisionId), now);
  await moderate(db, 'operator-a', action('suspend', 'voter', 0), now);
  await expect(moderate(db, 'operator-a', action('restore_vote', 'voter', 4, revisionId), now)).rejects.toThrow('moderation_ineligible');
  await moderate(db, 'operator-a', action('unsuspend', 'voter', 1), now);
  await moderate(db, 'operator-a', action('quarantine', revisionId, 4), now);
  await expect(moderate(db, 'operator-a', action('restore_vote', 'voter', 4, revisionId), now)).rejects.toThrow('moderation_ineligible');
  await moderate(db, 'operator-a', action('restore', revisionId, 5), now);
  await moderate(db, 'operator-a', action('restore_vote', 'voter', 4, revisionId), now);
});

it('keeps audit immutable and rejects unsupported commands, reason controls and excessive text', async () => {
  await expect(db.prepare("UPDATE moderation_actions SET reason = 'rewritten'").run()).rejects.toThrow('immutable_audit');
  await expect(db.prepare('DELETE FROM moderation_actions').run()).rejects.toThrow('immutable_audit');
  for (const reason of ['', '   ', '\u0000', 'x'.repeat(1001)])
    await expect(moderate(db, 'operator-a', { ...action('quarantine', revisionId, 6), reason }, now)).rejects.toThrow();
  await expect(moderate(db, 'operator-a', action('delete', revisionId, 6), now)).rejects.toThrow();
  await expect(inspectModeration(db, 'revision', 'missing')).rejects.toThrow('resource_not_found');
});