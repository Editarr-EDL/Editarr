import type { SqlDatabase } from '../runtime/contracts';
import { base64, receiptKeysSchema, type ReceiptKey } from '../security/service-signer';
import type { MembershipCheck } from '../security/contributor-auth';
import { contributorRoute, failure } from './contributor-route';

/** Query bound for the signed master read routes. */
export const MASTER_READ_QUERIES = 64;

export interface MasterReadOptions { receiptKeys: ReceiptKey[] }

/** Encode a JSON body with its RFC 9530 `Content-Digest`, as the receipt-key route always has. */
async function digestResponse(value: unknown): Promise<Response> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return new Response(bytes, { headers: { 'Content-Type': 'application/json', 'Content-Length': String(bytes.length),
    'Content-Digest': `sha-256=:${base64(digest)}:`, 'Cache-Control': 'private, no-store, no-transform' } });
}

/** Serve `GET /v1/receipt-keys` (§7.7): the service signing-key history. */
export async function contributorMasterRead(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: MasterReadOptions): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (path !== '/v1/receipt-keys') return failure(404, 'not_found');
  return contributorRoute(request, db, { authority, now, membership, method: 'GET', invalid: 'invalid_request',
    maxBodyBytes: 0, maxQueries: MASTER_READ_QUERIES },
  async () => digestResponse({ version: 1, service: `https://${authority}`, keys: receiptKeysSchema.parse(options.receiptKeys) }));
}
