import type { SqlDatabase } from '../runtime/contracts';
import { parseDictionary } from 'structured-headers';
import { verifyRequestSignature, RequestSignatureTimeError } from './request-signatures';
import { readBoundedBytes } from '../http/bounded-body';

export type AuthenticatedContributor = {
  contributorId: string;
  keyId: string;
  displayName: string;
  nonce: string;
  operationId: string | null;
  contentDigest: string;
  expiresAt: number;
};

export class ContributorAuthError extends Error {
  constructor(public code: 'invalid_signature' | 'clock_skew' | 'key_inactive' | 'membership_unavailable' |
    'membership_ineligible') {
    super(code);
  }
}

export type MembershipCheck = (contributorId: string, now: number) =>
  Promise<'eligible' | 'ineligible' | 'unavailable'>;

export async function authenticateContributor(request: Request, db: SqlDatabase,
  authority: string, now: number, membership?: MembershipCheck,
  options: { body?: Uint8Array; maxBodyBytes?: number } = {}): Promise<AuthenticatedContributor> {
  const signatureInput = request.headers.get('signature-input');
  let keyId: string;
  try {
    if (!signatureInput || signatureInput.length > 2048) throw new Error('missing');
    const dictionary = parseDictionary(signatureInput);
    const value = dictionary.get('editarr');
    if (dictionary.size !== 1 || !value || !Array.isArray(value[0]) ||
        typeof value[1].get('keyid') !== 'string') throw new Error('missing');
    keyId = value[1].get('keyid') as string;
    if (keyId.length < 1 || keyId.length > 128) throw new Error('invalid');
  } catch {
    throw new ContributorAuthError('invalid_signature');
  }
  const row = await db.prepare(`SELECT key.public_key, key.state AS key_state,
    contributor.contributor_id, contributor.display_name, contributor.state AS contributor_state
    FROM installation_keys AS key
    JOIN installations AS installation ON installation.installation_id = key.installation_id
    JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
    WHERE key.key_id = ?`).bind(keyId).first<{
      public_key: ArrayBuffer; key_state: string; contributor_id: string;
      display_name: string; contributor_state: string;
    }>();
  if (!row || row.key_state !== 'active' || row.contributor_state !== 'active') {
    throw new ContributorAuthError('key_inactive');
  }
  const contentLength = Number(request.headers.get('content-length') ?? 0);
  const bodyLimit = options.maxBodyBytes ?? 8192;
  if (!Number.isSafeInteger(bodyLimit) || bodyLimit <= 0 || !Number.isFinite(contentLength) || contentLength < 0 || contentLength > bodyLimit) {
    throw new ContributorAuthError('invalid_signature');
  }
  let body: Uint8Array;
  try { body = options.body ?? await readBoundedBytes(request, bodyLimit); }
  catch { throw new ContributorAuthError('invalid_signature'); }
  if (body.length > bodyLimit) throw new ContributorAuthError('invalid_signature');
  let verified: Awaited<ReturnType<typeof verifyRequestSignature>>;
  try {
    verified = await verifyRequestSignature({ method: request.method, url: request.url,
      headers: request.headers, body }, new Uint8Array(row.public_key), {
      expectedAuthority: authority, expectedKeyId: keyId, now,
      maxLifetimeSeconds: 120, maxClockSkewSeconds: 5,
    });
  } catch (error) {
    throw new ContributorAuthError(error instanceof RequestSignatureTimeError ? 'clock_skew' : 'invalid_signature');
  }
  const eligibility = membership ? await membership(row.contributor_id, now) : 'unavailable';
  if (eligibility === 'ineligible') throw new ContributorAuthError('membership_ineligible');
  if (eligibility !== 'eligible') {
    throw new ContributorAuthError('membership_unavailable');
  }
  const current = await db.prepare(`SELECT 1 AS active FROM installation_keys AS key
    JOIN installations AS installation ON installation.installation_id = key.installation_id
    JOIN contributors AS contributor ON contributor.contributor_id = installation.contributor_id
    WHERE key.key_id = ? AND key.state = 'active' AND contributor.state = 'active'
      AND contributor.contributor_id = ?`).bind(keyId, row.contributor_id).first();
  if (!current) throw new ContributorAuthError('key_inactive');
  return { contributorId: row.contributor_id, keyId, displayName: row.display_name,
    nonce: verified.nonce, operationId: verified.operationId,
    contentDigest: verified.contentDigest, expiresAt: verified.expires + 5 };
}