import { beforeAll, expect, it } from 'vitest';
import { reserveRevision, transitionPublication } from '../src/contributions/repository';
import { db, fixtureOperation, limits, rawRevision, setupCatalog, verifiedCase } from './helpers/catalog';

beforeAll(async () => {
  await setupCatalog();
  await reserveRevision(db, { verified: await verifiedCase('ai'), contributionId: 'initial-family', releaseId: 'initial-release', now: 2, limits, operation: await fixtureOperation('op-initial') });
});
const edge = (child: string, parent: string, digest = 'a'.repeat(64)) => db.prepare(`INSERT INTO contribution_parents VALUES (?, ?, ?)`).bind(child, parent, digest).run();
async function publish(id: string) {
  await transitionPublication(db, { revisionId: id, from: 'reserved', to: 'object_stored', objectEtag: 'fixture', now: 3 });
  await transitionPublication(db, { revisionId: id, from: 'object_stored', to: 'published', now: 4 });
}

it('rejects missing, self, forward, digest-mismatched and unsealed parents', async () => {
  await rawRevision('older', 'initial-release');
  await rawRevision('child', 'initial-release', { depth: 1 });
  await rawRevision('newer', 'initial-release');
  await expect(edge('child', 'missing')).rejects.toThrow('invalid_parent');
  await expect(edge('child', 'child')).rejects.toThrow('invalid_parent');
  await expect(edge('child', 'older')).rejects.toThrow('invalid_parent');
  await publish('older');
  await publish('newer');
  await expect(edge('child', 'newer')).rejects.toThrow('invalid_parent');
  await expect(edge('child', 'older', '0'.repeat(64))).rejects.toThrow('invalid_parent');
  await edge('child', 'older');
  await expect(edge('child', 'older')).rejects.toThrow();
  await publish('child');
  await expect(edge('older', 'child')).rejects.toThrow('invalid_parent');
  await expect(db.prepare(`UPDATE contribution_parents SET parent_digest = ? WHERE revision_id = 'child'`).bind('0'.repeat(64)).run()).rejects.toThrow('immutable_parent');
  await expect(db.prepare(`DELETE FROM contribution_parents WHERE revision_id = 'child'`).run()).rejects.toThrow('immutable_parent');
});

it('enforces parent count during concurrent inserts and checks depth at sealing', async () => {
  for (const parent of ['limit-parent-1', 'limit-parent-2']) {
    await rawRevision(parent, 'initial-release'); await publish(parent);
  }
  await rawRevision('limited-child', 'initial-release', { depth: 1, parentLimit: 1 });
  const results = await Promise.allSettled([edge('limited-child', 'limit-parent-1'), edge('limited-child', 'limit-parent-2')]);
  expect(results.filter(item => item.status === 'fulfilled')).toHaveLength(1);
  await publish('limited-child');
  await rawRevision('wrong-depth', 'initial-release', { depth: 2 });
  await edge('wrong-depth', 'limit-parent-1');
  await expect(publish('wrong-depth')).rejects.toThrow('invalid_lineage_depth');
});

it('requires published same-family supersedes and immutable historical key ownership', async () => {
  await rawRevision('family-root', 'initial-release', { family: 'family-history' });
  await expect(rawRevision('premature-successor', 'initial-release', { family: 'family-history', supersedes: 'family-root' })).rejects.toThrow('invalid_supersedes');
  await publish('family-root');
  await rawRevision('family-successor', 'initial-release', { family: 'family-history', supersedes: 'family-root' });
  await expect(rawRevision('other-family', 'initial-release', { family: 'other', supersedes: 'family-root' })).rejects.toThrow('invalid_supersedes');
  await db.prepare(`INSERT INTO contributors VALUES ('other-author', 'discord-other', 'Other', 'active', 1)`).run();
  await expect(rawRevision('wrong-owner', 'initial-release', { author: 'other-author', family: 'other-author-family' })).rejects.toThrow('revision_key_owner');
  await db.prepare(`UPDATE installation_keys SET state = 'retired', retired_at = 5 WHERE key_id = 'key-1'`).run();
  expect(await db.prepare(`SELECT contributor_id, key_id FROM contribution_revisions WHERE revision_id = 'family-root'`).first()).toEqual({ contributor_id: 'contributor-1', key_id: 'key-1' });
  await expect(db.prepare(`UPDATE contributions SET contributor_id = 'other-author' WHERE contribution_id = 'family-history'`).run()).rejects.toThrow('immutable_contribution');
  await expect(db.prepare(`DELETE FROM contribution_revisions WHERE revision_id = 'family-root'`).run()).rejects.toThrow('immutable_revision');
});