import { env } from './environment';
import payloads from '../../migrations/0010_d1_artifacts.sql?raw';
import localUsage from '../../migrations/0015_local_usage.sql?raw';
import budget from '../../migrations/0002_budget_ledger.sql?raw';
import quota from '../../migrations/0003_quotas.sql?raw';
import admission from '../../migrations/0004_request_admission.sql?raw';
import usage from '../../migrations/0006_oauth_usage.sql?raw';
import { setupCatalog, verifiedCase, limits as catalogLimits } from './catalog';
import type { PublicationLimits } from '../../src/contributions/publication-input';
import type { AuthenticatedContributor } from '../../src/security/contributor-auth';
import { signedArtifact } from './publication-signatures';

export const db = env.CATALOG_DB;
export const now = 1_700_000_030;
export const limits: PublicationLimits = { requestBytes: 131072, manifestBytes: 16384, validationWallMs: 10000,
  artifact: { compressedBytes: 65536, expandedBytes: 524288, events: 2500, text: 4096, depth: 24 }, catalog: catalogLimits };
export async function setupPublication() {
  await setupCatalog();
  for (const migration of [budget, quota, admission, usage, payloads, localUsage]) await db.exec(migration.replace(/\s*\n\s*/g, ' '));
}
export async function publicationCase(name: Parameters<typeof verifiedCase>[0]) {
  const verified = await verifiedCase(name);
  const identity: AuthenticatedContributor = { contributorId: 'contributor-1', keyId: 'key-1', displayName: 'Public Name',
    nonce: crypto.randomUUID(), operationId: crypto.randomUUID(), contentDigest: `fixture-${name}`, expiresAt: now + 60 };
  const artifact = structuredClone(verified.artifact.artifact);
  artifact.sharing_terms = 'community-reuse-1';
  return { identity, input: await signedArtifact(artifact) };
}