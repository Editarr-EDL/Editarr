import type { Env } from '../config';
import { recordUsage } from '../budgets/usage';
import { completeEnrollmentProof, EnrollmentProofError } from '../identity/enrollment-proof';
import { contributorProfile, generatedName } from '../identity/profile';
import { createBotMembership } from '../integrations/discord/membership';
import { admittedDiscordWork } from '../integrations/discord/service';
import { RequestSignatureTimeError } from '../security/request-signatures';

function failure(status: number, code: string) {
  return Response.json({ error: { code } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

/** Complete only a signed, locally confirmed, still-eligible Discord handoff. */
export async function contributorEnrollment(request: Request, env: Env, now = Date.now(),
  client: typeof fetch = fetch): Promise<Response> {
  if (request.method !== 'POST' || request.url !== `${env.PUBLIC_ORIGIN}/v1/enrollments/complete`) {
    return failure(400, 'invalid_enrollment_proof');
  }
  if (!env.POLICY_VERSION || !env.PUBLIC_ORIGIN?.startsWith('https://') || !env.CATALOG_DB) {
    return failure(503, 'service_unavailable');
  }
  return admittedDiscordWork(request, env, async (db, admit) => {
    try {
      const allowed = await admit(request);
      if (allowed !== 'allowed' || !db) return failure(503, 'service_unavailable');
      const original = request.clone() as Request;
      let enrolled: Awaited<ReturnType<typeof completeEnrollmentProof>> | undefined;
      for (let attempt = 0; attempt < 8; attempt += 1) {
        try {
          enrolled = await completeEnrollmentProof(original.clone() as Request, db, {
            authority: new URL(env.PUBLIC_ORIGIN!).host, now, maxLifetimeSeconds: 120,
            maxClockSkewSeconds: 5, displayName: generatedName(), requireConfirmation: true,
            beforeRegister: async (subject) => {
              const state = await createBotMembership({ ...env, CATALOG_DB: db }, client,
                undefined, subject)('', Math.floor(now / 1000));
              if (state !== 'eligible') throw new Error(`membership_${state}`);
            },
          });
          break;
        } catch (error) {
          if (!(error instanceof EnrollmentProofError) || error.code !== 'display_name_taken') throw error;
        }
      }
      if (!enrolled) return failure(503, 'name_generation_unavailable');
      const response = Response.json(await contributorProfile(db, enrolled.contributorId,
        enrolled.keyId, env.POLICY_VERSION!, now), { headers: { 'Cache-Control': 'no-store' } });
      try {
        await recordUsage(db, { contributorId: enrolled.contributorId, resource: 'worker_requests',
          outcome: 'allowed', units: 1, now });
      } catch { response.headers.set('Community-Usage-Status', 'unavailable'); }
      return response;
    } catch (error) {
      if (error instanceof RequestSignatureTimeError) return failure(401, 'clock_skew');
      if (error instanceof EnrollmentProofError) return failure(error.code === 'invalid_enrollment_proof' ?
        400 : 409, error.code);
      if (String(error).includes('membership_ineligible')) return failure(403, 'membership_ineligible');
      if (String(error).includes('membership_unavailable')) return failure(503, 'membership_unavailable');
      return failure(503, 'service_unavailable');
    }
  }, now);
}