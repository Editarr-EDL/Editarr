import type { ResourceBounds } from '../artifact/codec';
import { positive, token } from '../catalog/records';
import { MAX_STORED_BYTES } from './storage';
import type { CatalogLimits } from './repository';

export interface PublicationLimits {
  requestBytes: number;
  manifestBytes: number;
  validationWallMs: number;
  artifact: ResourceBounds;
  catalog: CatalogLimits;
}
export interface PublicationInput {
  contributionId: string; submissionId: string;
  artifactBytes: Uint8Array; manifestBytes: Uint8Array; envelopeBytes: Uint8Array;
}

export function publicationLimits(value: unknown): PublicationLimits {
  const input = value as PublicationLimits | null;
  if (!input || Object.keys(input).sort().join(',') !== 'artifact,catalog,manifestBytes,requestBytes,validationWallMs' ||
      !input.artifact || Object.keys(input.artifact).sort().join(',') !== 'compressedBytes,depth,events,expandedBytes,text' ||
      !input.catalog || Object.keys(input.catalog).sort().join(',') !== 'maxJsonBytes,maxLineageDepth,maxParents,maxReadQueries,maxStatements')
    throw new Error('publication_unavailable');
  for (const limit of [input.requestBytes, input.manifestBytes, input.validationWallMs,
    ...Object.values(input.artifact), ...Object.values(input.catalog)]) positive(limit);
  // Leave room for metadata below the SQLite 2 MB row limit.
  if (input.artifact.compressedBytes + input.manifestBytes + 256 > MAX_STORED_BYTES)
    throw new Error('publication_unavailable');
  return input;
}

function decode(value: unknown, limit: number) {
  if (typeof value !== 'string' || value.length > Math.ceil(limit / 3) * 4 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value))
    throw new Error('invalid_publication');
  const decoded = atob(value);
  if (!decoded.length || decoded.length > limit || btoa(decoded) !== value) throw new Error('invalid_publication');
  return Uint8Array.from(decoded, character => character.charCodeAt(0));
}

export function publicationInput(value: unknown, limits: PublicationLimits): PublicationInput {
  const input = value as Record<string, unknown> | null;
  if (!input || Object.keys(input).sort().join(',') !== 'artifact,contributionId,envelope,manifest,submissionId,version' ||
      input.version !== 1 || typeof input.contributionId !== 'string' || typeof input.submissionId !== 'string')
    throw new Error('invalid_publication');
  return { contributionId: token(input.contributionId), submissionId: token(input.submissionId),
    artifactBytes: decode(input.artifact, limits.artifact.compressedBytes),
    manifestBytes: decode(input.manifest, limits.manifestBytes), envelopeBytes: decode(input.envelope, 256) };
}