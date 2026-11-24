import type { SqlDatabase } from '../runtime/contracts';
import type { MasterPolicy } from '../filters/policy';
import type { MergeResult } from '../filters';
import { ArtifactV3Error } from '../artifact/v3';
import { verifySubmission } from '../artifact/signatures';
import type { AuthenticatedContributor } from '../security/contributor-auth';
import { planVersionAcceptance, VersionAcceptanceError, type AcceptancePlan, type VersionAcceptance } from '../versions/acceptance-service';
import { isCatalogWriteConflict } from '../versions/works';
import { storedKey } from '../versions/keys';
import { readWorkRows, withPendingWork } from '../versions/work-summary';
import { recomputeStatements } from '../master/recompute';
import { loadVersionState, type VersionState } from '../master/state';
import { RouteError } from '../http/contributor-route';
import { completedOperation, completionStatements } from '../operations/completed';
import { artifactBounds, type SubmissionLimits, type SubmissionRequest } from './input';
import { submissionInput, submissionStatements, type StoredSubmission } from './store';

export interface SubmissionResult {
  version: 1; operationId: string; submissionId: string; submissionDigest: string; workId: string; versionId: string;
  versionStatus: 'confirmed' | 'candidate'; disposition: VersionAcceptance['disposition']; tier: '1' | '2' | '3' | null;
  revision: number; supersedes: string | null;
  outcome: { newFilters: number; supports: number; disputes: number; coverageNegatives: number; ignored: number; unassigned: number };
  aliasSuggestion: VersionAcceptance['aliasSuggestion'];
}
export interface SubmissionOptions { service: string; policy: MasterPolicy; limits: SubmissionLimits; now: number }

const completed = (db: SqlDatabase, identity: AuthenticatedContributor) => completedOperation(db, identity, 'submission');

/** Replay a stored result for the same operation and bytes, or refuse a different upload of the same `submissionId`. */
async function replay(db: SqlDatabase, identity: AuthenticatedContributor, request: SubmissionRequest): Promise<SubmissionResult | null> {
  const stored = await db.prepare(`SELECT contributor_id, artifact_bytes, manifest_bytes, envelope_bytes FROM submissions
    WHERE submission_id = ?`).bind(request.submissionId).first<{ contributor_id: string; artifact_bytes: ArrayBuffer;
      manifest_bytes: ArrayBuffer; envelope_bytes: ArrayBuffer }>();
  if (!stored) return null;
  const same = stored.contributor_id === identity.contributorId && [[stored.artifact_bytes, request.artifactBytes],
    [stored.manifest_bytes, request.manifestBytes], [stored.envelope_bytes, request.envelopeBytes]]
    .every(([left, right]) => Buffer.from(left as ArrayBuffer).equals(right as Uint8Array));
  const operation = await completed(db, identity);
  if (!same || !operation) throw new RouteError(409, 'submission_conflict');
  return JSON.parse(operation.result) as SubmissionResult;
}

/** The state of the version a plan accepts into, with the pending acceptance and submission applied. */
async function pendingState(db: SqlDatabase, plan: AcceptancePlan, stored: StoredSubmission): Promise<VersionState> {
  const acceptance = plan.result, keys = stored.artifact.media.matchKeys.map(storedKey);
  const seq = (await db.prepare('SELECT coalesce(max(accepted_seq), 0) + 1 AS next FROM version_acceptances').first<{ next: number }>())!.next;
  let state: VersionState;
  if (acceptance.disposition === 'attached') {
    state = (await loadVersionState(db, acceptance.versionId))!;
  } else {
    const labels = await db.prepare('SELECT alias_id, label FROM aliases').all<{ alias_id: string; label: string }>();
    state = { versionId: acceptance.versionId, workId: acceptance.workId, status: acceptance.versionStatus,
      aliasId: acceptance.label.aliasId, labelText: acceptance.label.labelText, quarantined: false, revision: 1, latest: null,
      workRows: plan.work.created ? { works: [], identifiers: [], titles: [] } : await readWorkRows(db, [acceptance.workId]),
      labels: new Map(labels.results.map(row => [row.alias_id, row.label])),
      referenceKeys: keys.map(key => key.algorithm ? { type: key.type, algorithm: key.algorithm, value: key.value }
        : { type: key.type, value: key.value, sizeBytes: key.sizeBytes }),
      submissions: [], votes: [], registry: [], quarantinedContributors: [], quarantinedSubmissions: [],
      quarantinedAttestations: [], sourceVersionIds: [acceptance.versionId] };
  }
  state.status = acceptance.versionStatus;
  state.workRows = withPendingWork(state.workRows, acceptance.workId, plan.work.kind, plan.work.attached, plan.work.title);
  state.submissions = [...state.submissions, submissionInput(stored, seq)];
  return state;
}

function outcome(result: MergeResult, submissionId: string, registry: readonly string[]): SubmissionResult['outcome'] {
  const own = result.attestations.filter(row => row.submissionId === submissionId), known = new Set(registry);
  return {
    newFilters: result.registry.filter(row => !known.has(row.filterId) && row.state === 'active').length,
    supports: own.filter(row => row.kind === 'support' && row.weight === 1).length,
    disputes: own.filter(row => row.kind === 'dispute' && row.filterId !== null).length,
    coverageNegatives: own.filter(row => row.kind === 'coverage_negative' && row.filterId !== null).length,
    ignored: own.filter(row => row.ignoredReason !== null).length,
    unassigned: own.filter(row => !row.implicit && row.kind !== 'support' && row.filterId === null).length,
  };
}

/**
 * Accept one signed `community-artifact-3` submission (§7.2): verify it against the request
 * signer, run version acceptance (§3.5), store the raw buffers and recompute the version's master
 * set (§6) in one batch, planning again after a concurrent catalog write.
 */
export async function acceptSubmission(db: SqlDatabase, identity: AuthenticatedContributor, request: SubmissionRequest,
  options: SubmissionOptions): Promise<SubmissionResult> {
  if (identity.operationId !== `submit-${request.submissionId}`) throw new RouteError(400, 'invalid_submission');
  const prior = await completed(db, identity);
  if (prior?.request_digest === identity.contentDigest) return JSON.parse(prior.result) as SubmissionResult;
  const replayed = await replay(db, identity, request);
  if (replayed) return replayed;
  const signer = await db.prepare(`SELECT k.public_key, k.installation_id, c.quarantined FROM installation_keys k
    JOIN installations i USING (installation_id) JOIN contributors c ON c.contributor_id = i.contributor_id
    WHERE k.key_id = ? AND k.state = 'active' AND c.contributor_id = ?`).bind(identity.keyId, identity.contributorId)
    .first<{ public_key: ArrayBuffer; installation_id: string; quarantined: number }>();
  if (!signer) throw new Error('key_inactive');
  if (signer.quarantined === 1) throw new RouteError(403, 'upload_suspended');
  const { decoded } = await verifySubmission(request.artifactBytes, request.manifestBytes, request.envelopeBytes,
    { contributorId: identity.contributorId, keyId: identity.keyId, publicKey: new Uint8Array(signer.public_key) },
    artifactBounds(options.limits));
  if (decoded.artifact.submissionId !== request.submissionId) throw new ArtifactV3Error('invalid_submission');
  const window = await db.prepare(`SELECT count(*) AS count, min(created_at) AS oldest FROM submissions
    WHERE contributor_id = ? AND created_at > ?`).bind(identity.contributorId, options.now - 86_400).first<{ count: number; oldest: number | null }>();
  if (window && window.count >= options.policy.submissionsPerDay)
    throw new RouteError(429, 'rate_limited', {}, { 'Retry-After': String(Math.max(1, window.oldest! + 86_400 - options.now)) });
  const artifact = decoded.artifact;
  const input = { submissionId: artifact.submissionId, contributorId: identity.contributorId, work: artifact.work,
    version: artifact.version, durationMs: artifact.media.durationMs, matchKeys: artifact.media.matchKeys };
  for (let attempt = 1; ; attempt++) {
    let plan: AcceptancePlan;
    try { plan = await planVersionAcceptance(db, input, { policy: options.policy, now: options.now }); }
    catch (error) {
      if (error instanceof VersionAcceptanceError) throw new RouteError(error.code === 'invalid_submission' ? 400 : 422, error.code);
      throw error;
    }
    const stored: StoredSubmission = { submissionId: artifact.submissionId, digest: decoded.digest, contributorId: identity.contributorId,
      installationId: signer.installation_id, keyId: identity.keyId, versionId: plan.result.versionId,
      tier: plan.result.tier === null ? null : Number(plan.result.tier) as 1 | 2 | 3, artifact,
      artifactBytes: request.artifactBytes, manifestBytes: request.manifestBytes, envelopeBytes: request.envelopeBytes };
    const state = await pendingState(db, plan, stored);
    const previous = state.submissions.filter(item => item.contributorId === identity.contributorId &&
      item.submissionId !== stored.submissionId && !state.quarantinedSubmissions.includes(item.submissionId))
      .sort((a, b) => b.acceptedSeq - a.acceptedSeq)[0];
    const registry = state.registry.map(row => row.filterId);
    const recompute = await recomputeStatements(db, state, { service: options.service, policy: options.policy, now: options.now });
    const result: SubmissionResult = { version: 1, operationId: identity.operationId, submissionId: stored.submissionId,
      submissionDigest: stored.digest, workId: plan.result.workId, versionId: plan.result.versionId,
      versionStatus: plan.result.versionStatus, disposition: plan.result.disposition, tier: plan.result.tier,
      revision: recompute.revision, supersedes: previous?.submissionId ?? null,
      outcome: outcome(recompute.result, stored.submissionId, registry), aliasSuggestion: plan.result.aliasSuggestion };
    try {
      await db.batch([
        ...completionStatements(db, identity, 'submission', identity.contentDigest, result, options.now),
        ...plan.statements, ...submissionStatements(db, stored, options.now), ...recompute.statements,
      ]);
      return result;
    } catch (error) {
      const raced = await replay(db, identity, request).catch(() => null);
      if (raced) return raced;
      const text = String(error);
      if (text.includes('artifact_storage_exhausted')) throw new RouteError(503, 'service_unavailable');
      if (/UNIQUE constraint failed: (submissions|version_acceptances)\.(submission_id|digest)/.test(text) ||
          text.includes('operation_conflict')) throw new RouteError(409, 'submission_conflict');
      if (attempt >= 5 || !isCatalogWriteConflict(error)) throw error;
    }
  }
}

