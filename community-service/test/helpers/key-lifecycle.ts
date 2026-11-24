import type { SqlDatabase } from '../../src/runtime/contracts';
import { applyBaseline } from './schema';
import { publicKeyId, rotationProofBytes, type RotationIntent } from '../../src/identity/rotation-proof';
import { signedEnrollment } from './enrollment-signatures';

export const origin = 'https://community.example';
export const now = 1_700_000_030;
export type SigningKey = { keyId: string; publicKey: Uint8Array; privateKey: CryptoKey };

export async function prepareKeyDatabase(db: import('./environment').FixtureDatabase) {
  await applyBaseline(db);
}

export async function signingKey(): Promise<SigningKey> {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  return { keyId: await publicKeyId(publicKey), publicKey, privateKey: pair.privateKey };
}

export async function registerKey(db: SqlDatabase, contributorId = crypto.randomUUID()) {
  const key = await signingKey();
  const installationId = crypto.randomUUID();
  const discordId = String(1554889314549243900n + BigInt(Math.floor(Math.random() * 1_000_000_000)));
  const name = `Name ${contributorId}`;
  await db.batch([
    db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
      VALUES (?, ?, ?, ?, 1) ON CONFLICT(contributor_id) DO NOTHING`)
      .bind(contributorId, discordId, name, name.toLowerCase()),
    db.prepare('INSERT INTO installations (installation_id, contributor_id, created_at) VALUES (?, ?, 1)')
      .bind(installationId, contributorId),
    db.prepare(`INSERT INTO installation_keys (key_id, installation_id, algorithm, public_key, state, created_at)
      VALUES (?, ?, 'Ed25519', ?, 'active', 1)`).bind(key.keyId, installationId, key.publicKey),
  ]);
  return { ...key, installationId, contributorId, discordId };
}

export async function preparedRotation(old: SigningKey & { installationId: string }, operationId = crypto.randomUUID(),
  candidate?: SigningKey) {
  const replacement = candidate ?? await signingKey();
  const body: RotationIntent = { version: 1, installationId: old.installationId, oldKeyId: old.keyId,
    newPublicKey: btoa(String.fromCharCode(...replacement.publicKey)), oldProof: '', newProof: '' };
  for (const role of ['old', 'new'] as const) {
    const signature = new Uint8Array(await crypto.subtle.sign('Ed25519',
      role === 'old' ? old.privateKey : replacement.privateKey,
      rotationProofBytes(body, replacement.keyId, 'community.example', operationId, role) as BufferSource));
    body[role === 'old' ? 'oldProof' : 'newProof'] = btoa(String.fromCharCode(...signature));
  }
  return { replacement, operationId, body: JSON.stringify(body) };
}

export async function signedPost(key: SigningKey, path: string, body: string, operationId: string,
  timestamp = now, nonce = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(16))))
    .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')) {
  return signedEnrollment({ ...key, installationId: crypto.randomUUID(), stateId: operationId }, timestamp * 1000,
    origin + path, { body, operationId, nonce });
}

export async function signedRead(key: SigningKey, path: string, timestamp = now) {
  const contentDigest = 'sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:';
  const signatureInput = `editarr=("@method" "@scheme" "@authority" "@path" "@query" "content-digest");` +
    `created=${timestamp - 10};expires=${timestamp + 50};nonce="AQIDBAUGBwgJCgsMDQ4PEA";` +
    `keyid="${key.keyId}";alg="ed25519";tag="editarr-community-request-v1"`;
  const base = `"@method": GET\n"@scheme": https\n"@authority": community.example\n"@path": ${path}\n` +
    `"@query": ?\n"content-digest": ${contentDigest}\n"@signature-params": ${signatureInput.slice(8)}`;
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', key.privateKey, new TextEncoder().encode(base)));
  return new Request(origin + path, { headers: { 'content-digest': contentDigest, 'signature-input': signatureInput,
    signature: `editarr=:${btoa(String.fromCharCode(...signature))}:` } });
}