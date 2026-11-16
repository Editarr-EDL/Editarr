import { env } from './environment';
import identity from '../../migrations/0001_identity_operations.sql?raw';
import catalog from '../../migrations/0009_catalog.sql?raw';
import fixture from '../../../tests/fixtures/community/donation-artifacts-v2.json';
import { verifyArtifactSignature } from '../../src/artifact/signatures';

export const db = env.CATALOG_DB;
export const limits = { maxParents: 4, maxLineageDepth: 4, maxJsonBytes: 16384, maxReadQueries: 10, maxStatements: 40 };
export const decode = (value: string) => Uint8Array.from(atob(value), char => char.charCodeAt(0));
export async function setupCatalog() {
  await db.exec(identity.replace(/\s*\n\s*/g, ' '));
  await db.exec(catalog.replace(/\s*\n\s*/g, ' '));
  await db.prepare(`INSERT INTO contributors VALUES ('contributor-1', 'discord-1', 'Public Name', 'active', 1)`).run();
  await db.prepare(`INSERT INTO installations VALUES ('installation-1', 'contributor-1', 1)`).run();
  await db.prepare(`INSERT INTO installation_keys VALUES ('key-1', 'installation-1', 'Ed25519', ?, 'active', 1, NULL)`)
    .bind(decode(fixture.public_key)).run();
}
export async function verifiedCase(name: keyof typeof fixture.cases) {
  const item = fixture.cases[name];
  return verifyArtifactSignature(decode(item.artifact), decode(item.manifest), decode(item.envelope),
    { contributorId: 'contributor-1', keyId: 'key-1', publicKey: decode(fixture.public_key), status: 'active' },
    `submission-${name}`, { compressedBytes: 65536, expandedBytes: 524288, events: 100, text: 4096, depth: 24 });
}

export async function rawRevision(id: string, releaseId: string, input: {
  family?: string; depth?: number; parentLimit?: number; supersedes?: string; author?: string; key?: string;
} = {}) {
  const family = input.family ?? 'raw-family', author = input.author ?? 'contributor-1';
  await db.batch([
    db.prepare(`INSERT INTO operations VALUES (?, 'fixture-publication', ?, 'fixture-digest', 'pending', NULL, 1, 1)`).bind(author, id),
    db.prepare(`INSERT INTO contributions (contribution_id, contributor_id, created_at) VALUES (?, ?, 1)
      ON CONFLICT(contribution_id) DO NOTHING`).bind(family, author),
    db.prepare(`INSERT INTO contribution_revisions (revision_id, contribution_id, contributor_id,
      operation_kind, operation_id,
      key_id, release_id, kind, artifact_schema, artifact_digest, manifest_digest, envelope_digest,
      artifact_bytes, manifest_bytes, envelope_bytes, object_key, summary_json, total_events, audio_events, video_events, lineage_depth, parent_limit, supersedes, created_at)
      VALUES (?, ?, ?, 'fixture-publication', ?, ?, ?, 'manual', 'community-artifact-2', ?, ?, ?, 1, 1, 1, ?, '{}', 0, 0, 0, ?, ?, ?, 2)`)
      .bind(id, family, author, id, input.key ?? 'key-1', releaseId, 'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64),
        `artifacts/fixture-${id}`, input.depth ?? 0, input.parentLimit ?? 4, input.supersedes ?? null),
    db.prepare(`INSERT INTO contribution_publications VALUES (?, 'reserved', NULL, 2)`).bind(id),
    db.prepare(`INSERT INTO contribution_statistics (revision_id) VALUES (?)`).bind(id),
  ]);
}

export async function fixtureOperation(id: string) {
  await db.prepare(`INSERT INTO operations VALUES ('contributor-1', 'fixture-publication', ?, 'fixture-digest', 'pending', NULL, 1, 1)
    ON CONFLICT(contributor_id, kind, operation_id) DO NOTHING`).bind(id).run();
  return { kind: 'fixture-publication', id };
}