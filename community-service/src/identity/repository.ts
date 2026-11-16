import type { SqlDatabase } from '../runtime/contracts';
import { nameKey } from './profile';

export type Contributor = { contributor_id: string; discord_user_id: string };
export type Enrollment = {
  stateId: string;
  discordUserId: string;
  contributorId: string;
  installationId: string;
  keyId: string;
  publicKey: Uint8Array;
  displayName: string;
  now: number;
  confirmationHash?: string;
  proof?: { nonce: string; expiresAt: number };
};

export async function enroll(db: SqlDatabase, input: Enrollment): Promise<Contributor> {
  const state = await db.prepare(`SELECT installation_id, key_id, public_key FROM enrollment_states
    WHERE state_id = ? AND consumed_at IS NULL AND expires_at > ? AND
    provider_subject = ?`).bind(
    input.stateId, input.now, input.discordUserId,
  ).first<{ installation_id: string; key_id: string; public_key: ArrayBuffer }>();
  if (!state || state.installation_id !== input.installationId || state.key_id !== input.keyId ||
      !bytesEqual(new Uint8Array(state.public_key), input.publicKey)) {
    throw new Error('enrollment_state_invalid');
  }

  await db.batch([
    db.prepare(`UPDATE enrollment_states SET consumed_at = ?
      WHERE state_id = ? AND consumed_at IS NULL AND expires_at > ?
        AND provider_subject = ? AND installation_id = ? AND key_id = ? AND public_key = ?
        ${input.confirmationHash ? 'AND confirmation_hash = ?' : ''}`).bind(
      input.now, input.stateId, input.now, input.discordUserId,
      input.installationId, input.keyId, input.publicKey,
      ...(input.confirmationHash ? [input.confirmationHash] : []),
    ),
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, created_at${input.confirmationHash ? ', display_name_key' : ''})
      VALUES (CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?, ?, ?${input.confirmationHash ? ', ?' : ''})
      ON CONFLICT(discord_user_id) DO NOTHING`).bind(
      input.contributorId, input.discordUserId, input.displayName, input.now,
      ...(input.confirmationHash ? [nameKey(input.displayName)] : []),
    ),
    db.prepare(`INSERT INTO installations (installation_id, contributor_id, created_at)
      SELECT ?, contributor_id, ? FROM contributors WHERE discord_user_id = ?
      `).bind(
      input.installationId, input.now, input.discordUserId,
    ),
    db.prepare(`INSERT INTO installation_keys
      (key_id, installation_id, algorithm, public_key, state, created_at)
      SELECT ?, ?, 'Ed25519', ?, 'active', ? FROM installations
      WHERE installation_id = ? AND contributor_id =
        (SELECT contributor_id FROM contributors WHERE discord_user_id = ?)
      `).bind(
      input.keyId, input.installationId, input.publicKey, input.now,
      input.installationId, input.discordUserId,
    ),
    ...(input.proof ? [db.prepare(`INSERT INTO request_nonces (key_id, nonce, expires_at, contributor_id)
      SELECT ?, ?, ?, contributor_id FROM contributors WHERE discord_user_id = ?`).bind(
      input.keyId, input.proof.nonce, input.proof.expiresAt, input.discordUserId,
    )] : []),
  ]);
  const contributor = await db.prepare(`SELECT contributor_id, discord_user_id FROM contributors
    WHERE discord_user_id = ?`).bind(input.discordUserId).first<Contributor>();
  if (!contributor) throw new Error('enrollment_conflict');
  return contributor;
}

function bytesEqual(left: Uint8Array, right: Uint8Array): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export async function replaceInstallationKey(db: SqlDatabase, installationId: string,
  oldKeyId: string, newKeyId: string, publicKey: Uint8Array, now: number): Promise<void> {
  const results = await db.batch([
    db.prepare(`UPDATE installation_keys SET state = 'retired', retired_at = ?
      WHERE key_id = ? AND installation_id = ? AND state = 'active'`).bind(
      now, oldKeyId, installationId,
    ),
    db.prepare(`INSERT INTO installation_keys
      (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (CASE WHEN changes() = 1 THEN ? ELSE NULL END, ?, 'Ed25519', ?, 'active', ?)`).bind(
      newKeyId, installationId, publicKey, now,
    ),
  ]);
  if (results[0].meta.changes !== 1 || results[1].meta.changes !== 1) {
    throw new Error('key_replacement_conflict');
  }
}