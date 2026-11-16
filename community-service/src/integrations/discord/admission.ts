import type { OAuthAdmission } from './oauth';

type AdmissionDependencies = {
  trustedActorKey?: (request: Request) => string | null;
  policy?: (request: Request) => Promise<'allowed' | 'paused' | 'unavailable'>;
};

/** Require trusted ingress before broker storage or provider calls. */
export function createOAuthAdmission(dependencies: AdmissionDependencies): OAuthAdmission {
  return async (request) => {
    try {
      const { trustedActorKey, policy } = dependencies;
      if (!trustedActorKey || !policy) return 'unavailable';
      const actor = trustedActorKey(request);
      if (!actor || actor.length > 256 || !/^[\x21-\x7e]+$/.test(actor)) return 'unavailable';
      const admission = await policy(request);
      return admission === 'allowed' ? 'allowed' : 'unavailable';
    } catch {
      return 'unavailable';
    }
  };
}
