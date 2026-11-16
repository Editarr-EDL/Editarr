import { z } from 'zod';
import { publicBytes, publicKeyId } from '../identity/rotation-proof';
import { sha256 } from '../catalog/records';

const token = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
const digest = z.string().regex(/^[a-f0-9]{64}$/);
const service = z.string().max(256).refine(value => {
  try { const url = new URL(value); return url.protocol === 'https:' && url.origin === value; }
  catch { return false; }
});
const keyId = z.string().regex(/^sha256:[a-f0-9]{64}$/);
const receiptSchema = z.strictObject({
  schemaVersion: z.literal('community-download-receipt-1'), service, serviceKeyId: keyId,
  contributorId: token, revisionId: token, artifactDigest: digest, receiptId: digest,
  issuedAt: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),
});
const envelopeSchema = z.strictObject({ receipt: receiptSchema, signature: z.string().max(88) });
const keySchema = z.strictObject({ keyId, publicKey: z.string().max(44), status: z.enum(['active', 'retired', 'revoked']) });
export const receiptKeysSchema = z.array(keySchema).min(1).max(16)
  .refine(keys => new Set(keys.map(key => key.keyId)).size === keys.length);
export type ReceiptKey = z.infer<typeof keySchema>;
export type DownloadReceipt = z.infer<typeof envelopeSchema>;
export type ReceiptBinding = Pick<DownloadReceipt['receipt'], 'service' | 'contributorId' | 'revisionId' | 'artifactDigest'>;
export interface ReceiptSigner { keyId: string; privateKey: CryptoKey }
const encoder = new TextEncoder();

export function base64(bytes: Uint8Array) {
  let value = '';
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}

function framed(domain: string, fields: string[], issuedAt?: number) {
  const prefix = encoder.encode(`${domain}\0`), encoded = fields.map(field => encoder.encode(field));
  const result = new Uint8Array(prefix.length + encoded.reduce((total, field) => total + 4 + field.length, 0) + (issuedAt === undefined ? 0 : 8));
  result.set(prefix);
  const view = new DataView(result.buffer);
  let offset = prefix.length;
  for (const field of encoded) {
    view.setUint32(offset, field.length); offset += 4;
    result.set(field, offset); offset += field.length;
  }
  if (issuedAt !== undefined) view.setBigUint64(offset, BigInt(issuedAt));
  return result;
}

export async function receiptIdentity(binding: ReceiptBinding) {
  return sha256(framed('editarr-community-download-identity-v1',
    [binding.service, binding.contributorId, binding.revisionId, binding.artifactDigest]));
}
/** Encode fixed-order fields and Unix seconds under the receipt-specific Ed25519 domain. */
export function receiptSigningBytes(receipt: DownloadReceipt['receipt']) {
  receiptSchema.parse(receipt);
  return framed('editarr-community-download-receipt-v1', [receipt.schemaVersion, receipt.service,
    receipt.serviceKeyId, receipt.contributorId, receipt.revisionId, receipt.artifactDigest, receipt.receiptId], receipt.issuedAt);
}

/** Load a non-extractable service key only when it matches the configured active public key. */
export async function loadReceiptSigner(privateKeyBase64: string, keyValues: unknown): Promise<ReceiptSigner> {
  try {
    const keys = receiptKeysSchema.parse(keyValues);
    const active = keys.filter(key => key.status === 'active');
    if (active.length !== 1) throw new Error('invalid');
    for (const key of keys) if (await publicKeyId(publicBytes(key.publicKey, 32)) !== key.keyId) throw new Error('invalid');
    if (typeof privateKeyBase64 !== 'string' || privateKeyBase64.length > 256) throw new Error('invalid');
    const bytes = Uint8Array.from(atob(privateKeyBase64), character => character.charCodeAt(0));
    if (base64(bytes) !== privateKeyBase64) throw new Error('invalid');
    const privateKey = await crypto.subtle.importKey('pkcs8', bytes as BufferSource, 'Ed25519', false, ['sign']);
    const publicKey = await crypto.subtle.importKey('raw', publicBytes(active[0].publicKey, 32) as BufferSource, 'Ed25519', false, ['verify']);
    const challenge = encoder.encode('editarr-community-download-key-check-v1');
    const signed = await crypto.subtle.sign('Ed25519', privateKey, challenge);
    if (!await crypto.subtle.verify('Ed25519', publicKey, signed, challenge)) throw new Error('invalid');
    return { keyId: active[0].keyId, privateKey };
  } catch { throw new Error('receipt_signer_unavailable'); }
}

export async function issueReceipt(signer: ReceiptSigner, binding: ReceiptBinding, issuedAt: number): Promise<DownloadReceipt> {
  const receipt = receiptSchema.parse({ schemaVersion: 'community-download-receipt-1', ...binding,
    serviceKeyId: signer.keyId, receiptId: await receiptIdentity(binding), issuedAt });
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', signer.privateKey, receiptSigningBytes(receipt) as BufferSource));
  return { receipt, signature: base64(signature) };
}

/** Verify attribution against independently trusted service key history, never response-supplied trust. */
export async function verifyReceipt(value: unknown, expected: ReceiptBinding, trustedKeys: ReceiptKey[]): Promise<DownloadReceipt> {
  try {
    const envelope = envelopeSchema.parse(value), receipt = envelope.receipt;
    if (Object.entries(expected).some(([field, content]) => receipt[field as keyof ReceiptBinding] !== content) ||
        receipt.receiptId !== await receiptIdentity(expected)) throw new Error('invalid');
    const keys = receiptKeysSchema.parse(trustedKeys);
    const key = keys.find(item => item.keyId === receipt.serviceKeyId);
    if (!key || key.status === 'revoked') throw new Error('invalid');
    const bytes = publicBytes(key.publicKey, 32);
    if (await publicKeyId(bytes) !== key.keyId) throw new Error('invalid');
    const publicKey = await crypto.subtle.importKey('raw', bytes as BufferSource, 'Ed25519', false, ['verify']);
    if (!await crypto.subtle.verify('Ed25519', publicKey, publicBytes(envelope.signature, 64) as BufferSource,
      receiptSigningBytes(receipt) as BufferSource)) throw new Error('invalid');
    return envelope;
  } catch { throw new Error('invalid_download_receipt'); }
}