import type { SqlDatabase } from '../runtime/contracts';
import type { MasterPolicy } from '../filters/policy';
import { ArtifactV3Error } from '../artifact/v3';
import type { MembershipCheck } from '../security/contributor-auth';
import { parseSubmissionRequest, type SubmissionLimits } from '../submissions/input';
import { acceptSubmission } from '../submissions/service';
import { contributorRoute, json, jsonBody, RouteError } from './contributor-route';

/** Query bound for one submission, including acceptance and recompute retries. */
const SUBMISSION_QUERIES = 800;

/** Serve `POST /v1/submissions` (§7.2). */
export async function contributorSubmission(request: Request, db: SqlDatabase, authority: string, now: number,
  membership: MembershipCheck, options: { policy: MasterPolicy; limits: SubmissionLimits }): Promise<Response> {
  return contributorRoute(request, db, { authority, now, membership, method: 'POST', invalid: 'invalid_submission',
    maxBodyBytes: options.limits.requestBytes, maxQueries: SUBMISSION_QUERIES }, async context => {
    let input;
    try { input = parseSubmissionRequest(jsonBody(context.body), options.limits); }
    catch { throw new RouteError(400, 'invalid_submission'); }
    try {
      const result = await acceptSubmission(context.db, context.identity, input,
        { service: `https://${authority}`, policy: options.policy, limits: options.limits, now });
      return json({ version: 1, state: 'completed', result });
    } catch (error) {
      if (error instanceof ArtifactV3Error) throw new RouteError(error.code === 'invalid_submission' ? 400 : 422, error.code);
      throw error;
    }
  });
}
