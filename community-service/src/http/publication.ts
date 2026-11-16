import type { SqlDatabase } from '../runtime/contracts';
import { ArtifactError } from '../artifact/codec';
import { ArtifactSignatureError } from '../artifact/signatures';
import { recordUsage } from '../budgets/usage';
import { publicationInput, publicationLimits, type PublicationLimits } from '../contributions/publication-input';
import { activePublicationKey, publicationOperation, publishArtifact } from '../contributions/publication';
import { readReservation } from '../contributions/repository';
import { authenticateContributor, ContributorAuthError, type MembershipCheck } from '../security/contributor-auth';
import { BodyLimitError, readBoundedBytes } from './bounded-body';

function response(status: number, value: unknown) {
  return Response.json(value, { status, headers: { 'Cache-Control': 'no-store' } });
}
function failure(status: number, code: string) { return response(status, { error: { code } }); }

export async function contributorPublication(request: Request, db: SqlDatabase,
  authority: string, now: number, membership: MembershipCheck, limits: PublicationLimits): Promise<Response> {
  const path = new URL(request.url).pathname;
  const read = /^\/v1\/publication-operations\/([A-Za-z0-9_.-]{1,128})$/.exec(path);
  if (read ? request.method !== 'GET' : request.method !== 'POST') return failure(405, 'method_not_allowed');
  try {
    publicationLimits(limits);
    if ((!read && path !== '/v1/contributions') || request.url !== `https://${authority}${path}` ||
        request.headers.has('content-encoding') || (!read && request.headers.get('content-type') !== 'application/json'))
      return failure(400, 'invalid_publication');
    const body = await readBoundedBytes(request, read ? 0 : limits.requestBytes);
    const identity = await authenticateContributor(request, db, authority, now, membership,
      { body, maxBodyBytes: read ? 8192 : limits.requestBytes });
    if (read) {
      const row = await publicationOperation(db, identity.contributorId, read[1]);
      const reservation = await readReservation(db, identity.contributorId, { kind: 'publication', id: read[1] });
      if (!await activePublicationKey(db, identity)) throw new ContributorAuthError('key_inactive');
      const result = row?.state === 'completed' && reservation?.state === 'published' && row.result ? JSON.parse(row.result) : null;
      const output = row && reservation ? response(200, { version: 1, state: row.state, publicationState: reservation.state, result }) : failure(404, 'operation_not_found');
      try { await recordUsage(db, { contributorId: identity.contributorId, resource: 'worker_requests', outcome: 'allowed', units: 1, now: now * 1000 }); }
      catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
      try { await recordUsage(db, { contributorId: identity.contributorId, resource: 'd1_writes', outcome: 'allowed',
        units: 2, now: now * 1000 }); }
      catch { output.headers.set('Community-Usage-Status', 'unavailable'); }
      return output;
    }
    const published = await publishArtifact(db, identity,
      () => publicationInput(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)), limits), limits, now);
    const output = response(200, { version: 1, state: 'completed', publicationState: 'published', result: published.result });
    if (!published.usageAvailable) output.headers.set('Community-Usage-Status', 'unavailable');
    return output;
  } catch (error) {
    if (error instanceof BodyLimitError) return failure(413, 'body_too_large');
    if (error instanceof ContributorAuthError) return failure(error.code === 'membership_unavailable' ? 503 :
      error.code === 'membership_ineligible' ? 403 : 401, error.code);
    if (error instanceof ArtifactSignatureError) return failure(400, 'invalid_artifact_signature');
    if (error instanceof ArtifactError) return failure(400, 'invalid_artifact');
    if (error instanceof SyntaxError || error instanceof TypeError) return failure(400, 'invalid_publication');
    const message = String(error);
    if (message.includes('key_inactive')) return failure(401, 'key_inactive');
    if (message.includes('validation_budget_exceeded') || message.includes('publication_size_limit')) return failure(413, 'publication_limit');
    if (message.includes('invalid_publication')) return failure(400, 'invalid_publication');
    if (message.includes('sharing_permission_required')) return failure(400, 'sharing_permission_required');
    for (const code of ['source_permission_unknown', 'source_permission_incompatible', 'source_attribution_mismatch'])
      if (message.includes(code)) return failure(400, code);
    if (message.includes('contribution_revisions.revision_id')) return failure(409, 'revision_conflict');
    if (['missing_parent', 'invalid_parent', 'conflicting_parent_digest', 'revision_author_mismatch'].some(code => message.includes(code)))
      return failure(400, 'invalid_lineage');
    if (['parent_limit', 'lineage_depth_limit', 'catalog_read_limit', 'catalog_statement_limit', 'catalog_json_limit'].some(code => message.includes(code)))
      return failure(413, 'publication_limit');
    for (const code of ['operation_conflict', 'object_conflict', 'publication_not_available', 'publication_retry_required'])
      if (message.includes(code)) return failure(409, code);
    if (message.includes('request_nonces')) return failure(409, 'nonce_conflict');
    return failure(503, 'service_unavailable');
  }
}