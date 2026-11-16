import type { SqlDatabase } from '../runtime/contracts';
import { transientStatement } from '../retention/transient';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { nameKey } from './profile';

/** Commit a name and replay record together. */
export async function renameContributor(db: SqlDatabase, identity: AuthenticatedContributor,
  name: string, now: number): Promise<'changed' | 'existing'> {
  if (!identity.operationId || !Number.isSafeInteger(now) || now < 0) throw new Error('invalid_rename');
  const operationId = identity.operationId;
  async function previous() {
    const row = await db.prepare(`SELECT request_digest, state FROM operations
      WHERE contributor_id = ? AND kind = 'profile_name' AND operation_id = ?`)
      .bind(identity.contributorId, operationId).first<{ request_digest: string; state: string }>();
    if (!row) return false;
    if (row.request_digest !== identity.contentDigest || row.state !== 'completed') {
      throw new Error('operation_conflict');
    }
    return true;
  }
  if (await previous()) return 'existing';
  try {
    const results = await db.batch([
      db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id)
        VALUES (CASE WHEN EXISTS (SELECT 1 FROM installation_keys AS key
          JOIN installations AS installation ON installation.installation_id = key.installation_id
          JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
          WHERE key.key_id = ? AND key.state = 'active' AND contributor.state = 'active'
            AND contributor.contributor_id = ?) THEN ? ELSE NULL END, ?, ?, ?)`)
        .bind(identity.keyId, identity.contributorId, identity.keyId, identity.nonce,
          identity.expiresAt, identity.contributorId),
      db.prepare(`INSERT INTO operations
        (contributor_id, kind, operation_id, request_digest, state, result, created_at, updated_at)
        VALUES (?, 'profile_name', ?, ?, 'completed', ?, ?, ?)
        ON CONFLICT(contributor_id, kind, operation_id) DO NOTHING`)
        .bind(identity.contributorId, operationId, identity.contentDigest, JSON.stringify({ displayName: name }), now, now),
      db.prepare(`INSERT INTO profile_name_changes (contributor_id, operation_id, changed_at)
        SELECT contributor_id, ?, ? FROM contributors WHERE contributor_id = ? AND display_name != ?
          AND changes() = 1`).bind(operationId, now, identity.contributorId, name),
      db.prepare(`UPDATE contributors SET
        profile_updated_at = CASE WHEN display_name != ? THEN MAX(profile_updated_at + 1, ?) ELSE profile_updated_at END,
        display_name = ?, display_name_key = ? WHERE contributor_id = ? AND changes() = 1`)
        .bind(name, now, name, nameKey(name), identity.contributorId),
    ]);
    if (results[1].meta.changes === 0) {
      if (!await previous()) throw new Error('operation_conflict');
      return 'existing';
    }
    return 'changed';
  } catch (error) {
    if (await previous()) return 'existing';
    if (String(error).includes('uq_contributors_display_name') ||
        String(error).includes('contributors.display_name')) throw new Error('display_name_taken');
    if (String(error).includes('request_nonces')) throw new Error('nonce_conflict');
    throw error;
  }
}

export async function pruneProfileNames(db: SqlDatabase, now: number, limit: number): Promise<void> {
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000) {
    throw new Error('invalid_limit');
  }
  await transientStatement(db, 'profileNameEvents', now, limit, 'prune').run();
}