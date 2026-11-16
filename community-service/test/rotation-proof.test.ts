import { expect, it } from 'vitest';
import { publicBytes, publicKeyId, rotationIntent, rotationProofBytes, verifyRotationProofs,
  type RotationIntent } from '../src/identity/rotation-proof';
import contract from '../../tests/fixtures/community/key-rotation-v1.json';

it('matches independently generated RFC 8032 key proof bytes shared with Python', async () => {
  const intent = rotationIntent(JSON.parse(contract.requestBody));
  expect(intent).toEqual(contract.intent);
  for (const role of ['old', 'new'] as const) {
    expect(new TextDecoder().decode(rotationProofBytes(intent, contract.newKeyId,
      contract.authority, contract.operationId, role))).toBe(contract.proofMessages[role]);
  }
  const replacement = await verifyRotationProofs(intent, publicBytes(contract.oldPublicKey, 32),
    contract.authority, contract.operationId);
  expect(replacement.keyId).toBe(contract.newKeyId);
});

it('requires both proofs bound to role, service, installation and exact operation', async () => {
  const oldPair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const newPair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const oldPublic = new Uint8Array(await crypto.subtle.exportKey('raw', oldPair.publicKey));
  const newPublic = new Uint8Array(await crypto.subtle.exportKey('raw', newPair.publicKey));
  const newKeyId = await publicKeyId(newPublic);
  const body: RotationIntent = { version: 1, installationId: crypto.randomUUID(), oldKeyId: await publicKeyId(oldPublic),
    newPublicKey: btoa(String.fromCharCode(...newPublic)), oldProof: '', newProof: '' };
  for (const role of ['old', 'new'] as const) {
    const signature = new Uint8Array(await crypto.subtle.sign('Ed25519',
      role === 'old' ? oldPair.privateKey : newPair.privateKey,
      rotationProofBytes(body, newKeyId, 'community.example', 'prepared-operation', role) as BufferSource));
    body[role === 'old' ? 'oldProof' : 'newProof'] = btoa(String.fromCharCode(...signature));
  }
  expect(rotationIntent(body)).toEqual(body);
  expect((await verifyRotationProofs(body, oldPublic, 'community.example', 'prepared-operation')).keyId).toBe(newKeyId);
  for (const changed of [{ ...body, installationId: crypto.randomUUID() },
    { ...body, newProof: body.oldProof }, { ...body, oldProof: body.newProof }]) {
    await expect(verifyRotationProofs(changed, oldPublic, 'community.example', 'prepared-operation')).rejects.toThrow();
  }
  await expect(verifyRotationProofs(body, oldPublic, 'wrong.example', 'prepared-operation')).rejects.toThrow();
  await expect(verifyRotationProofs(body, oldPublic, 'community.example', 'other-operation')).rejects.toThrow();
  expect(() => rotationIntent({ ...body, discordId: 'not-accepted' })).toThrow();
});

it('rejects noncanonical or wrong-length public key/proof encodings', () => {
  for (const value of [null, '', 'a'.repeat(44), btoa('short'), 'A'.repeat(42) + 'B=']) {
    expect(() => publicBytes(value, 32)).toThrow();
  }
});