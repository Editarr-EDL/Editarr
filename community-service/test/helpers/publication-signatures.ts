import { parseArtifact } from '../../src/artifact/codec';
import type { Artifact } from '../../src/artifact/schema';
import { sha256 } from '../../src/catalog/records';
import { verifiedCase } from './catalog';
import vectors from '../../../tests/fixtures/community/donation-artifacts-v2.json';
import { limits } from './publication';

const encoder = new TextEncoder();
export const encode = (value: unknown) => encoder.encode(JSON.stringify(value));
export const base64 = (bytes: Uint8Array) => {
  let text = '';
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
};
export async function fixtureKey() {
  const prefix = Uint8Array.from('302e020100300506032b657004220420'.match(/../g)!, pair => Number.parseInt(pair, 16));
  const bytes = new Uint8Array(48);
  bytes.set(prefix); bytes.set(Uint8Array.from({ length: 32 }, (_, index) => index), 16);
  return { keyId: 'key-1', publicKey: Uint8Array.from(atob(vectors.public_key), character => character.charCodeAt(0)),
    privateKey: await crypto.subtle.importKey('pkcs8', bytes, 'Ed25519', false, ['sign']) };
}

export async function signManifest(manifestBytes: Uint8Array) {
  const manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
  const chunks = [encoder.encode('editarr-community-artifact-signature-v1\0')];
  for (const field of ['schema_version', 'artifact_schema_version', 'artifact_format', 'submission_id', 'contributor_id', 'key_id']) {
    const bytes = encoder.encode(manifest[field]);
    const chunk = new Uint8Array(bytes.length + 4);
    new DataView(chunk.buffer).setUint32(0, bytes.length); chunk.set(bytes, 4); chunks.push(chunk);
  }
  chunks.push(new Uint8Array(await crypto.subtle.digest('SHA-256', manifestBytes as BufferSource)),
    Uint8Array.from(manifest.artifact_digest.match(/../g)!, (pair: string) => Number.parseInt(pair, 16)));
  const base = new Uint8Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { base.set(chunk, offset); offset += chunk.length; }
  const key = await fixtureKey();
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', key.privateKey, base));
  return encode({ schema_version: 'community-artifact-signature-1', signature: base64(signature) });
}

export async function signedArtifact(artifact: Artifact, stored = false) {
  const expanded = encode(artifact);
  const artifactBytes = new Uint8Array(await new Response(new Blob([expanded as BlobPart]).stream()
    .pipeThrough(new CompressionStream('gzip'))).arrayBuffer());
  const bounds = { ...limits.artifact, expandedBytes: 4 * 1024 * 1024 };
  const parsed = await parseArtifact(artifactBytes, bounds, true, stored);
  const manifestBytes = encode({ schema_version: 'community-manifest-1', artifact_format: 'gzip-json',
    artifact_schema_version: artifact.schema_version, artifact_digest: await sha256(artifactBytes),
    submission_id: artifact.submission_id, contributor_id: artifact.author.contributor_id,
    key_id: artifact.author.key_id, summary: parsed.summary });
  return { contributionId: crypto.randomUUID(), submissionId: artifact.submission_id,
    artifactBytes, manifestBytes, envelopeBytes: await signManifest(manifestBytes), expandedBytes: expanded.length, bounds };
}

export async function syntheticPublication(events = 4) {
  const original = await verifiedCase('ai');
  const artifact = structuredClone(original.artifact.artifact);
  artifact.submission_id = crypto.randomUUID();
  artifact.sharing_terms = 'community-reuse-1';
  artifact.findings = Array.from({ length: events }, (_, index) => ({ ...artifact.findings[0],
    event_id: `event-${index}`, note: `Synthetic public note ${index}; generated validation workload, not personal media.` }));
  return signedArtifact(artifact);
}

export function uploadBody(input: Awaited<ReturnType<typeof syntheticPublication>>) {
  return JSON.stringify({ version: 1, contributionId: input.contributionId, submissionId: input.submissionId,
    artifact: base64(input.artifactBytes), manifest: base64(input.manifestBytes), envelope: base64(input.envelopeBytes) });
}