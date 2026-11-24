import type { SqlDatabase } from '../runtime/contracts';
import { base64, receiptKeysSchema, type ReceiptKey, type ReceiptSigner } from '../security/service-signer';
import type { MembershipCheck } from '../security/contributor-auth';
import { downloadMaster, knownRevision } from '../master/download';
import { contributorRoute, failure, json, RouteError } from './contributor-route';

/** Query bound for the signed master read routes. */
export const MASTER_READ_QUERIES = 64;

export interface MasterReadOptions { receiptKeys: ReceiptKey[]; signer?: ReceiptSigner; versionChecksPerHour?: number }

/** Encode a JSON body with its RFC 9530 `Content-Digest`, as the receipt-key route always has. */
async function digestResponse(value: unknown): Promise<Response> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return new Response(bytes, { headers: { 'Content-Type': 'application/json', 'Content-Length': String(bytes.length),
    'Content-Digest': `sha-256=:${base64(digest)}:`, 'Cache-Control': 'private, no-store, no-transform' } });
}

const MASTER = /^\/v1\/versions\/([^/]+)\/master$/;
const TOKEN = /^[A-Za-z0-9_.-]{1,128}$/;

/** Serve `GET /v1/receipt-keys` (§7.7) and `GET /v1/versions/{versionId}/master` (§7.3). */
export async function contributorMasterRead(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: MasterReadOptions): Promise<Response> {
  const path = new URL(request.url).pathname, master = MASTER.exec(path);
  if (path === '/v1/receipt-keys') return contributorRoute(request, db, { authority, now, membership, method: 'GET',
    invalid: 'invalid_request', maxBodyBytes: 0, maxQueries: MASTER_READ_QUERIES },
  async () => digestResponse({ version: 1, service: `https://${authority}`, keys: receiptKeysSchema.parse(options.receiptKeys) }));
  if (!master || !options.signer || !options.versionChecksPerHour) return failure(404, 'not_found');
  const { signer, versionChecksPerHour } = options;
  return contributorRoute(request, db, { authority, now, membership, method: 'GET', invalid: 'invalid_master_request',
    maxBodyBytes: 0, maxQueries: MASTER_READ_QUERIES, query: true }, async context => {
    if (!TOKEN.test(master[1])) throw new RouteError(400, 'invalid_master_request');
    const known = knownRevision(context.url.searchParams);
    return json(await downloadMaster(context.db, context.identity, master[1], known,
      { service: `https://${authority}`, signer, versionChecksPerHour, now }));
  });
}
