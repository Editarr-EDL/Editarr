import { expect, it } from 'vitest';
import { base64, issueReceipt, loadReceiptSigner, receiptSigningBytes, verifyReceipt, type ReceiptKey } from '../src/contributions/receipts';
import { publicKeyId } from '../src/identity/rotation-proof';
import { sha256 } from '../src/catalog/records';
import vector from '../../tests/fixtures/community/download-receipt-v1.json';

async function signer() {
  const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  const keys: ReceiptKey[] = [{ keyId: await publicKeyId(publicKey), publicKey: base64(publicKey), status: 'active' }];
  const secret = base64(new Uint8Array(await crypto.subtle.exportKey('pkcs8', pair.privateKey)));
  return { keys, secret, signer: await loadReceiptSigner(secret, keys) };
}
const binding = { service: 'https://community.example', contributorId: 'reader', revisionId: 'revision', artifactDigest: 'a'.repeat(64) };

it('issues fresh issuance signatures with a stable contributor/revision/digest identity and no expiry', async () => {
  const fixture = await signer();
  const first = await issueReceipt(fixture.signer, binding, 100), second = await issueReceipt(fixture.signer, binding, 101);
  expect(first.receipt.receiptId).toBe(second.receipt.receiptId);
  expect(first.signature).not.toBe(second.signature);
  expect(await verifyReceipt(first, binding, fixture.keys)).toEqual(first);
  expect(await verifyReceipt(first, binding, [{ ...fixture.keys[0], status: 'retired' }])).toEqual(first);
  await expect(verifyReceipt(first, binding, [{ ...fixture.keys[0], status: 'revoked' }])).rejects.toThrow('invalid_download_receipt');
  for (const field of ['service', 'contributorId', 'revisionId', 'artifactDigest'] as const) {
    await expect(verifyReceipt(first, { ...binding, [field]: field === 'artifactDigest' ? 'b'.repeat(64) : 'changed' }, fixture.keys))
      .rejects.toThrow('invalid_download_receipt');
  }
  await expect(verifyReceipt({ ...first, receipt: { ...first.receipt, issuedAt: 101 } }, binding, fixture.keys)).rejects.toThrow('invalid_download_receipt');
  await expect(verifyReceipt({ ...first, receipt: { ...first.receipt, extra: true } }, binding, fixture.keys)).rejects.toThrow('invalid_download_receipt');
});

it('fails closed on missing, mismatched, revoked or untrusted service keys', async () => {
  const first = await signer(), other = await signer();
  await expect(loadReceiptSigner(first.secret, other.keys)).rejects.toThrow('receipt_signer_unavailable');
  await expect(loadReceiptSigner('', first.keys)).rejects.toThrow('receipt_signer_unavailable');
  await expect(loadReceiptSigner(first.secret, [{ ...first.keys[0], status: 'retired' }])).rejects.toThrow('receipt_signer_unavailable');
  const receipt = await issueReceipt(first.signer, binding, 100);
  await expect(verifyReceipt(receipt, binding, other.keys)).rejects.toThrow('invalid_download_receipt');
  const rotated = await issueReceipt(other.signer, binding, 101);
  expect(rotated.receipt.receiptId).toBe(receipt.receipt.receiptId);
});

it('matches the independently generated Node Ed25519 byte/signature vector', async () => {
  const seed = Uint8Array.from(vector.syntheticSeedHex.match(/../g)!, pair => Number.parseInt(pair, 16));
  const prefix = Uint8Array.from('302e020100300506032b657004220420'.match(/../g)!, pair => Number.parseInt(pair, 16));
  const secret = new Uint8Array(prefix.length + seed.length); secret.set(prefix); secret.set(seed, prefix.length);
  const receipt = vector.envelope.receipt;
  const keys: ReceiptKey[] = [{ keyId: receipt.serviceKeyId, publicKey: vector.publicKey, status: 'active' }];
  const signer = await loadReceiptSigner(base64(secret), keys);
  const binding = { service: receipt.service, contributorId: receipt.contributorId,
    revisionId: receipt.revisionId, artifactDigest: receipt.artifactDigest };
  const issued = await issueReceipt(signer, binding, receipt.issuedAt);
  expect(issued).toEqual(vector.envelope);
  expect(await sha256(receiptSigningBytes(issued.receipt))).toBe(vector.signingBytesSha256);
  expect(await verifyReceipt(vector.envelope, binding, keys)).toEqual(issued);
});