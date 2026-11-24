import { ArtifactV3Error, decodeArtifactV3, parseManifestV3, type ArtifactBounds, type DecodedArtifactV3,
  type ManifestV3 } from './v3';

const decoder = new TextDecoder('utf-8', { fatal: true });

/** The registered signing key that authenticated the upload request. */
export interface SigningKey { contributorId: string; keyId: string; publicKey: Uint8Array }

/** Ed25519 message of `community-artifact-signature-1` (§5.1): framed manifest identity, manifest hash, artifact digest. */
export async function signatureBase(manifestBytes: Uint8Array, manifest: ManifestV3): Promise<Uint8Array> {
  const encoder = new TextEncoder();
  const parts = [encoder.encode('editarr-community-artifact-signature-v1\0')];
  for (const field of [manifest.schema_version, manifest.artifact_schema_version, manifest.artifact_format,
    manifest.submission_id, manifest.contributor_id, manifest.key_id]) {
    const bytes = encoder.encode(field), length = new Uint8Array(4);
    new DataView(length.buffer).setUint32(0, bytes.length);
    parts.push(length, bytes);
  }
  parts.push(new Uint8Array(await crypto.subtle.digest('SHA-256', manifestBytes as BufferSource)));
  parts.push(Uint8Array.from(manifest.artifact_digest.match(/../g)!, pair => Number.parseInt(pair, 16)));
  const result = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) { result.set(part, offset); offset += part.length; }
  return result;
}

/** Read the exact canonical envelope `{"schema_version":"community-artifact-signature-1","signature":"…"}`. */
export function readEnvelope(bytes: Uint8Array): Uint8Array {
  let text: string, value: unknown;
  try { text = decoder.decode(bytes); value = JSON.parse(text); }
  catch { throw new ArtifactV3Error('invalid_submission', 'envelope'); }
  const envelope = value as Record<string, unknown> | null;
  if (bytes.length > 256 || !envelope || typeof envelope !== 'object' || typeof envelope.signature !== 'string' ||
      !/^[A-Za-z0-9+/]{86}==$/.test(envelope.signature) ||
      JSON.stringify({ schema_version: 'community-artifact-signature-1', signature: envelope.signature }) !== text)
    throw new ArtifactV3Error('invalid_submission', 'envelope');
  const signature = Uint8Array.from(atob(envelope.signature), character => character.charCodeAt(0));
  if (signature.length !== 64) throw new ArtifactV3Error('invalid_submission', 'envelope');
  return signature;
}

export interface VerifiedSubmission { decoded: DecodedArtifactV3; manifest: ManifestV3 }

/**
 * Decode and validate the artifact and manifest, require the author and manifest identity to
 * equal the request signer (`422 key_mismatch`), then verify the detached Ed25519 signature over
 * the original bytes. The three buffers are never re-serialized.
 */
export async function verifySubmission(artifactBytes: Uint8Array, manifestBytes: Uint8Array, envelopeBytes: Uint8Array,
  signer: SigningKey, bounds?: ArtifactBounds): Promise<VerifiedSubmission> {
  const decoded = await decodeArtifactV3(artifactBytes, bounds);
  const manifest = parseManifestV3(manifestBytes, decoded, bounds);
  const signature = readEnvelope(envelopeBytes);
  if (manifest.contributor_id !== signer.contributorId || manifest.key_id !== signer.keyId) throw new ArtifactV3Error('key_mismatch');
  let valid = false;
  try {
    const key = await crypto.subtle.importKey('raw', signer.publicKey as BufferSource, 'Ed25519', false, ['verify']);
    valid = await crypto.subtle.verify('Ed25519', key, signature as BufferSource, await signatureBase(manifestBytes, manifest) as BufferSource);
  } catch { valid = false; }
  if (!valid) throw new ArtifactV3Error('invalid_submission', 'signature');
  return { decoded, manifest };
}
