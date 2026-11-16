export type RotationIntent = {
  version: 1; installationId: string; oldKeyId: string; newPublicKey: string;
  oldProof: string; newProof: string;
};

export function publicBytes(value: unknown, length: number): Uint8Array {
  if (typeof value !== 'string' || value.length !== Math.ceil(length / 3) * 4) {
    throw new Error('invalid_rotation_proof');
  }
  try {
    const bytes = Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
    if (bytes.length !== length || btoa(String.fromCharCode(...bytes)) !== value) throw new Error('invalid');
    return bytes;
  } catch { throw new Error('invalid_rotation_proof'); }
}

export async function publicKeyId(publicKey: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', publicKey as BufferSource));
  return 'sha256:' + Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function rotationIntent(value: unknown): RotationIntent {
  const body = value as Record<string, unknown> | null;
  if (!body || Array.isArray(body) || Object.keys(body).sort().join(',') !==
    'installationId,newProof,newPublicKey,oldKeyId,oldProof,version' || body.version !== 1 ||
    typeof body.installationId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(body.installationId) ||
    typeof body.oldKeyId !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(body.oldKeyId)) {
    throw new Error('invalid_key_operation');
  }
  publicBytes(body.newPublicKey, 32);
  publicBytes(body.oldProof, 64);
  publicBytes(body.newProof, 64);
  return body as RotationIntent;
}

/** Domain-separated proof bytes remain stable across fresh HTTP nonces and retries. */
export function rotationProofBytes(intent: RotationIntent, newKeyId: string,
  authority: string, operationId: string, role: 'old' | 'new'): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(['editarr-key-rotation-v1', role,
    authority, operationId, intent.installationId, intent.oldKeyId, newKeyId, intent.newPublicKey]));
}

export async function verifyRotationProofs(intent: RotationIntent, oldPublicKey: Uint8Array,
  authority: string, operationId: string) {
  const newPublicKey = publicBytes(intent.newPublicKey, 32);
  const newKeyId = await publicKeyId(newPublicKey);
  if (newKeyId === intent.oldKeyId || await publicKeyId(oldPublicKey) !== intent.oldKeyId) {
    throw new Error('invalid_rotation_proof');
  }
  for (const role of ['old', 'new'] as const) {
    const publicKey = await crypto.subtle.importKey('raw',
      (role === 'old' ? oldPublicKey : newPublicKey) as BufferSource, 'Ed25519', false, ['verify']);
    if (!await crypto.subtle.verify('Ed25519', publicKey,
      publicBytes(role === 'old' ? intent.oldProof : intent.newProof, 64) as BufferSource,
      rotationProofBytes(intent, newKeyId, authority, operationId, role) as BufferSource)) {
      throw new Error('invalid_rotation_proof');
    }
  }
  return { keyId: newKeyId, publicKey: newPublicKey };
}