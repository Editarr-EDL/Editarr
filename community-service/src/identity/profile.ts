import type { SqlDatabase } from '../runtime/contracts';

const words = ['Amber', 'Birch', 'Cedar', 'Coral', 'Dawn', 'Elm', 'Fern', 'Frost',
  'Grove', 'Harbor', 'Iris', 'Jade', 'Lake', 'Maple', 'Moss', 'Opal', 'Pine',
  'Quartz', 'Rain', 'Reed', 'River', 'Silver', 'Snow', 'Spring', 'Stone', 'Summer',
  'Sun', 'Tide', 'Vale', 'Willow', 'Wind', 'Winter'];

export function generatedName(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return Array.from(bytes, (byte) => words[byte % words.length]).join(' ');
}

export function validatedName(value: unknown): string {
  if (typeof value !== 'string' || value.length > 160 || /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value)) {
    throw new Error('invalid_display_name');
  }
  const name = value.trim().normalize('NFC');
  const length = Array.from(name).length;
  if (length < 3 || length > 40) throw new Error('invalid_display_name');
  return name;
}

export function nameKey(name: string): string {
  return name.normalize('NFC').toLowerCase().normalize('NFC');
}

/** Read current labels on every response; cached names never establish identity. */
export async function contributorProfile(db: SqlDatabase, contributorId: string,
  keyId: string, policyVersion: string, now: number) {
  const row = await db.prepare(`SELECT contributor.display_name, contributor.profile_updated_at,
      contributor.state AS contributor_state, key.state AS key_state, installation.installation_id
    FROM contributors AS contributor
    JOIN installations AS installation ON installation.contributor_id = contributor.contributor_id
    JOIN installation_keys AS key ON key.installation_id = installation.installation_id
    WHERE contributor.contributor_id = ? AND key.key_id = ?`).bind(contributorId, keyId).first<{
      display_name: string; profile_updated_at: number; contributor_state: string;
      key_state: string; installation_id: string;
    }>();
  if (!row || row.contributor_state !== 'active' || row.key_state !== 'active' || !policyVersion) {
    throw new Error('profile_unavailable');
  }
  return { contributorId, shortId: contributorId.slice(0, 8), installationId: row.installation_id,
    keyId, keyState: row.key_state, displayName: row.display_name,
    profileRevision: row.profile_updated_at, policyVersion, protocolVersion: 1,
    allowedOperations: ['identity:read', 'identity:rename'] };
}