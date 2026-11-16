import type { SqlDatabase } from '../runtime/contracts';
import { sha256 } from '../catalog/records';

export type StoredPart = { bytes: Uint8Array; digest: string };
export type PublicationParts = { artifact: StoredPart; manifest: StoredPart; envelope: StoredPart };
export type PublicationMetadata = Record<keyof PublicationParts, { byteLength: number; digest: string }>;
export const MAX_STORED_BYTES = 1_900_000;

/** Validate exact signed bytes and prepare their immutable insert for the publication batch. */
export async function publicationStorageStatement(db: SqlDatabase, key: string, parts: PublicationParts) {
  if (!/^artifacts\/[0-9a-f-]{36}$/.test(key)) throw new Error('invalid_object_key');
  const values = [parts.artifact, parts.manifest, parts.envelope];
  if (values.reduce((total, part) => total + part.bytes.length, 0) > MAX_STORED_BYTES ||
      values.some(part => !part.bytes.length) || parts.envelope.bytes.length > 256)
    throw new Error('publication_size_limit');
  for (const part of values) {
    if (await sha256(part.bytes) !== part.digest) throw new Error('object_digest_mismatch');
  }
  // The update trigger permits byte-identical retries only; storage is charged once.
  return db.prepare(`INSERT INTO artifact_payloads
    (object_key, artifact, manifest, envelope, artifact_digest, manifest_digest, envelope_digest)
    VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(object_key) DO UPDATE SET
      artifact = excluded.artifact, manifest = excluded.manifest, envelope = excluded.envelope,
      artifact_digest = excluded.artifact_digest, manifest_digest = excluded.manifest_digest,
      envelope_digest = excluded.envelope_digest`)
    .bind(key, ...values.map(part => part.bytes), ...values.map(part => part.digest));
}

/** Internal exact-byte reader; callers must authorize access before returning a payload. */
export async function readPublication(db: SqlDatabase, key: string,
  expected?: PublicationMetadata): Promise<PublicationParts | null> {
  const row = await db.prepare(`SELECT artifact, manifest, envelope,
    artifact_digest, manifest_digest, envelope_digest FROM artifact_payloads WHERE object_key = ?`)
    .bind(key).first<{ artifact: number[]; manifest: number[]; envelope: number[];
      artifact_digest: string; manifest_digest: string; envelope_digest: string }>();
  if (!row) return null;
  const parts = { artifact: { bytes: new Uint8Array(row.artifact), digest: row.artifact_digest },
    manifest: { bytes: new Uint8Array(row.manifest), digest: row.manifest_digest },
    envelope: { bytes: new Uint8Array(row.envelope), digest: row.envelope_digest } };
  for (const name of ['artifact', 'manifest', 'envelope'] as const) {
    const part = parts[name];
    if (expected && (part.bytes.length !== expected[name].byteLength || part.digest !== expected[name].digest))
      throw new Error('artifact_metadata_mismatch');
    if (await sha256(part.bytes) !== part.digest) throw new Error('object_digest_mismatch');
  }
  return parts;
}
