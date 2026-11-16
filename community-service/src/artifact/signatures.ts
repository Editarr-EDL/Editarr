import { parseArtifact, parseManifest, type ResourceBounds } from './codec';

export class ArtifactSignatureError extends Error {}

const domain = new TextEncoder().encode('editarr-community-artifact-signature-v1\0');
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });

export interface HistoricalKey {
  contributorId: string;
  keyId: string;
  publicKey: Uint8Array;
  status: 'active' | 'retired' | 'compromised' | 'revoked';
}

function signatureBase(manifestBytes: Uint8Array, manifest: Awaited<ReturnType<
  typeof parseManifest>>['manifest']): Promise<Uint8Array> {
  const fields = [manifest.schema_version, manifest.artifact_schema_version,
    manifest.artifact_format, manifest.submission_id, manifest.contributor_id, manifest.key_id];
  const chunks = fields.map((field) => {
    const encoded = encoder.encode(field);
    const result = new Uint8Array(encoded.length + 4);
    new DataView(result.buffer).setUint32(0, encoded.length);
    result.set(encoded, 4);
    return result;
  });
  return crypto.subtle.digest('SHA-256', manifestBytes as BufferSource).then((digest) => {
    const manifestHash = new Uint8Array(digest);
    const artifactHash = Uint8Array.from(manifest.artifact_digest.match(/../g)!,
      (pair) => Number.parseInt(pair, 16));
    const size = domain.length + chunks.reduce((length, chunk) => length + chunk.length, 0) + 64;
    const result = new Uint8Array(size);
    let offset = 0;
    for (const chunk of [domain, ...chunks, manifestHash, artifactHash]) {
      result.set(chunk, offset);
      offset += chunk.length;
    }
    return result;
  });
}

function readEnvelope(bytes: Uint8Array): Uint8Array {
  if (bytes.length > 256 || bytes.length === 0) {
    throw new ArtifactSignatureError('Invalid detached signature envelope length');
  }
  let value: unknown;
  let text: string;
  try {
    text = decoder.decode(bytes);
    value = JSON.parse(text) as unknown;
  } catch {
    throw new ArtifactSignatureError('Invalid detached signature envelope');
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ArtifactSignatureError('Invalid detached signature envelope');
  }
  const envelope = value as Record<string, unknown>;
  if (envelope.schema_version !== 'community-artifact-signature-1' ||
    typeof envelope.signature !== 'string' ||
    !/^[A-Za-z0-9+/]{86}==$/.test(envelope.signature) ||
    JSON.stringify({ schema_version: envelope.schema_version, signature: envelope.signature }) !== text) {
    throw new ArtifactSignatureError('Noncanonical detached signature envelope');
  }
  const signature = Uint8Array.from(atob(envelope.signature), (char) => char.charCodeAt(0));
  if (signature.length !== 64 || btoa(String.fromCharCode(...signature)) !== envelope.signature) {
    throw new ArtifactSignatureError('Invalid Ed25519 signature bytes');
  }
  return signature;
}

export async function verifyArtifactSignature(
  artifactBytes: Uint8Array, manifestBytes: Uint8Array, envelopeBytes: Uint8Array,
  key: HistoricalKey, submissionId: string, bounds: ResourceBounds, stored = false,
) {
  if (key.status !== 'active' && key.status !== 'retired') {
    throw new ArtifactSignatureError('Key not trusted for historical attribution');
  }
  if (key.publicKey.length !== 32) throw new ArtifactSignatureError('Invalid public key');
  const artifact = await parseArtifact(artifactBytes, bounds, true, stored);
  const manifest = await parseManifest(manifestBytes, artifact, bounds);
  if (manifest.manifest.contributor_id !== key.contributorId ||
    manifest.manifest.key_id !== key.keyId || manifest.manifest.submission_id !== submissionId) {
    throw new ArtifactSignatureError('Historical key or submission binding mismatch');
  }
  const signature = readEnvelope(envelopeBytes);
  const base = await signatureBase(manifestBytes, manifest.manifest);
  let valid = false;
  try {
    const publicKey = await crypto.subtle.importKey('raw', key.publicKey as BufferSource,
      'Ed25519', false, ['verify']);
    valid = await crypto.subtle.verify('Ed25519', publicKey, signature as BufferSource,
      base as BufferSource);
  } catch {
    throw new ArtifactSignatureError('Invalid public key');
  }
  if (!valid) throw new ArtifactSignatureError('Detached signature does not match original bytes');
  return { artifact, manifest, artifactBytes, manifestBytes, envelopeBytes,
    contributorId: key.contributorId, keyId: key.keyId, keyStatus: key.status };
}