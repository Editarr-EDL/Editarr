import { parseArtifact, type ResourceBounds } from '../artifact/codec';
import type { Artifact } from '../artifact/schema';
import { sha256 } from '../catalog/records';

export const SHARING_PERMISSION = 'community-reuse-1';

export function requireSharingPermission(artifact: Artifact) {
  if (artifact.sharing_terms !== SHARING_PERMISSION)
    throw new Error('sharing_permission_required');
}

export async function validateParentPermission(artifact: Artifact, parent: {
  revision_id: string; artifact_digest: string; contributor_id: string;
  artifact: ArrayBuffer | null;
}, bounds: ResourceBounds) {
  if (!parent.artifact) throw new Error('source_permission_unknown');
  const bytes = new Uint8Array(parent.artifact);
  if (await sha256(bytes) !== parent.artifact_digest) throw new Error('source_permission_unknown');
  const original = await parseArtifact(bytes, bounds, true, true);
  if (original.artifact.sharing_terms !== SHARING_PERMISSION)
    throw new Error('source_permission_incompatible');
  if (original.artifact.submission_id !== parent.revision_id ||
      original.artifact.author.contributor_id !== parent.contributor_id)
    throw new Error('source_permission_unknown');
  if (artifact.schema_version === 'community-artifact-2' && artifact.sources.some(source =>
    source.reference?.submission_id === parent.revision_id && source.author_id !== parent.contributor_id))
    throw new Error('source_attribution_mismatch');
}