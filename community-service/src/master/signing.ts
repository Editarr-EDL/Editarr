import { base64, framed, sha256Hex, type ReceiptKey, type ReceiptSigner } from '../security/service-signer';
import { publicBytes } from '../identity/rotation-proof';

export const SNAPSHOT_SIGNATURE_SCHEMA = 'community-master-signature-1';
export const MASTER_RECEIPT_SCHEMA = 'community-master-receipt-1';

/** The values a snapshot signature and receipt bind (§7.3). */
export interface SnapshotBinding { service: string; versionId: string; revision: number; snapshotDigest: string }
export interface SnapshotSignature extends SnapshotBinding {
  schemaVersion: typeof SNAPSHOT_SIGNATURE_SCHEMA; serviceKeyId: string; issuedAt: number; signature: string;
}
export interface MasterReceipt extends SnapshotBinding {
  schemaVersion: typeof MASTER_RECEIPT_SCHEMA; serviceKeyId: string; contributorId: string; receiptId: string; issuedAt: number;
}

/** Signed bytes of a snapshot signature: framed fields ‖ u64be(issuedAt). */
export function snapshotSigningBytes(value: Omit<SnapshotSignature, 'signature'>) {
  return framed('editarr-community-master-snapshot-v1', [value.schemaVersion, value.service, value.serviceKeyId,
    value.versionId, String(value.revision), value.snapshotDigest], value.issuedAt);
}

/** `receiptId`: it omits `issuedAt`, so a repeated download returns the same ID. */
export async function receiptIdentity(binding: SnapshotBinding & { contributorId: string }) {
  return sha256Hex(framed('editarr-community-master-receipt-identity-v1', [binding.service, binding.contributorId,
    binding.versionId, String(binding.revision), binding.snapshotDigest]));
}

/** Signed bytes of a download receipt: framed fields including `receiptId` ‖ u64be(issuedAt). */
export function receiptSigningBytes(receipt: MasterReceipt) {
  return framed('editarr-community-master-receipt-v1', [receipt.schemaVersion, receipt.service, receipt.serviceKeyId,
    receipt.contributorId, receipt.versionId, String(receipt.revision), receipt.snapshotDigest, receipt.receiptId], receipt.issuedAt);
}

async function sign(signer: ReceiptSigner, bytes: Uint8Array) {
  return base64(new Uint8Array(await crypto.subtle.sign('Ed25519', signer.privateKey, bytes as BufferSource)));
}

export async function signSnapshot(signer: ReceiptSigner, binding: SnapshotBinding, issuedAt: number): Promise<SnapshotSignature> {
  const value = { schemaVersion: SNAPSHOT_SIGNATURE_SCHEMA, service: binding.service, serviceKeyId: signer.keyId,
    versionId: binding.versionId, revision: binding.revision, snapshotDigest: binding.snapshotDigest, issuedAt } as const;
  return { ...value, signature: await sign(signer, snapshotSigningBytes(value)) };
}

export async function issueMasterReceipt(signer: ReceiptSigner, binding: SnapshotBinding & { contributorId: string },
  issuedAt: number): Promise<{ receipt: MasterReceipt; signature: string }> {
  const receipt: MasterReceipt = { schemaVersion: MASTER_RECEIPT_SCHEMA, service: binding.service, serviceKeyId: signer.keyId,
    contributorId: binding.contributorId, versionId: binding.versionId, revision: binding.revision,
    snapshotDigest: binding.snapshotDigest, receiptId: await receiptIdentity(binding), issuedAt };
  return { receipt, signature: await sign(signer, receiptSigningBytes(receipt)) };
}

/** Verify an Ed25519 signature with a trusted, non-revoked key from the service key history. */
export async function verifyServiceSignature(keys: readonly ReceiptKey[], keyId: string, bytes: Uint8Array, signature: string) {
  const key = keys.find(item => item.keyId === keyId);
  if (!key || key.status === 'revoked') return false;
  const imported = await crypto.subtle.importKey('raw', publicBytes(key.publicKey, 32) as BufferSource, 'Ed25519', false, ['verify']);
  return crypto.subtle.verify('Ed25519', imported, publicBytes(signature, 64) as BufferSource, bytes as BufferSource);
}
