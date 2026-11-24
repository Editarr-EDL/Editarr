import { z } from 'zod';
import { publicBytes, publicKeyId } from '../identity/rotation-proof';

const keyId = z.string().regex(/^sha256:[a-f0-9]{64}$/);
const keySchema = z.strictObject({ keyId, publicKey: z.string().max(44), status: z.enum(['active', 'retired', 'revoked']) });
/** `DOWNLOAD_RECEIPT_KEYS`: the service signing-key history served by `GET /v1/receipt-keys`. */
export const receiptKeysSchema = z.array(keySchema).min(1).max(16)
  .refine(keys => new Set(keys.map(key => key.keyId)).size === keys.length);
export type ReceiptKey = z.infer<typeof keySchema>;
/** The active service key that signs master snapshots and download receipts. */
export interface ReceiptSigner { keyId: string; privateKey: CryptoKey }
const encoder = new TextEncoder();

export function base64(bytes: Uint8Array) {
  let value = '';
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value);
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes as BufferSource)),
    byte => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * `framed(domain, fields[, issuedAt])` from the contract conventions: the ASCII domain, a zero
 * byte, each UTF-8 field prefixed by its u32be byte length, then an optional u64be `issuedAt`.
 */
export function framed(domain: string, fields: readonly string[], issuedAt?: number) {
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
