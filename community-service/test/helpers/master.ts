import fixture from '../fixtures/community-v3/master-snapshot.json';
import { contributorMasterRead } from '../../src/http/master';
import { contributorSubmission } from '../../src/http/submissions';
import { DEFAULT_SUBMISSION_LIMITS } from '../../src/submissions/input';
import { loadReceiptSigner, type ReceiptSigner } from '../../src/security/service-signer';
import type { ArtifactV3 } from '../../src/artifact/v3';
import { artifact, encode, nonce, submitRequest, type Contributor } from './submissions';
import { signedPost, signedRead } from './key-lifecycle';
import { db, now, policy } from './versions';

export const eligible = async () => 'eligible' as const;
export const receiptKeys = fixture.receiptKeys as { keyId: string; publicKey: string; status: 'active' | 'retired' | 'revoked' }[];
const pkcs8 = Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'),
  Buffer.from(fixture.servicePrivateKeySeedHex, 'hex')]).toString('base64');
let signer: ReceiptSigner | undefined;

export type Reply = { status: number; headers: Headers; body: Record<string, any> };
export async function reply(response: Response): Promise<Reply> {
  return { status: response.status, headers: response.headers, body: await response.json() as Record<string, any> };
}

/** Submit an artifact for TMDB `tmdb` through the HTTP handler and return its result. */
export async function submitWork(who: Contributor, tmdb: string, overrides: Partial<ArtifactV3> = {}) {
  const value = artifact(who, { work: { ...artifact(who).work, tmdb }, ...overrides });
  const response = await contributorSubmission(await submitRequest(who, await encode(value, who)), db, 'community.example', now,
    eligible, { policy, limits: DEFAULT_SUBMISSION_LIMITS });
  const { body } = await reply(response);
  if (response.status !== 200) throw new Error(JSON.stringify(body));
  return body.result as Record<string, any>;
}

/** Download a version's master set and return the snapshot and receipt. */
export async function download(who: Contributor, versionId: string) {
  signer ??= await loadReceiptSigner(pkcs8, receiptKeys);
  const response = await contributorMasterRead(await signedRead(who, `/v1/versions/${versionId}/master`, now, nonce()), db,
    'community.example', now, eligible, { receiptKeys, signer, versionChecksPerHour: 1000 });
  const { body } = await reply(response);
  if (response.status !== 200) throw new Error(JSON.stringify(body));
  return { snapshot: JSON.parse(Buffer.from(body.snapshot, 'base64').toString()) as { revision: number; filters: Record<string, any>[] },
    receiptId: body.receipt.receipt.receiptId as string };
}

export async function post(who: Contributor, path: string, value: unknown, operationId: string = crypto.randomUUID(), at = now) {
  return signedPost(who, path, typeof value === 'string' ? value : JSON.stringify(value), operationId, at, nonce());
}
