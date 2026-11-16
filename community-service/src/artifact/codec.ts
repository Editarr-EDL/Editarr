import { parseTree, type Node } from 'jsonc-parser';

import taxonomy from './taxonomy.json';
import { artifactSchema, manifestSchema, allowedSettings, allowedSettingsV2, validateV2, type Artifact } from './schema';
import { withoutArrProvenance } from '../catalog/compatibility-inputs';

export type { Artifact } from './schema';

export class ArtifactError extends Error {}

export interface ResourceBounds {
  compressedBytes: number;
  expandedBytes: number;
  events: number;
  text: number;
  depth: number;
}

const taxonomyPairs = new Map(Object.entries(taxonomy).flatMap(([family, tokens]) =>
  tokens.map((token) => [token, family] as const)));
const encoder = new TextEncoder();

function inspect(node: Node, bounds: ResourceBounds, depth = 0): void {
  if (depth > bounds.depth) throw new ArtifactError('Artifact nesting exceeds configured depth');
  if (node.type === 'string' && encoder.encode(node.value as string).length > bounds.text) {
    throw new ArtifactError('Artifact text exceeds configured length');
  }
  if (node.type === 'object') {
    const keys = new Set<string>();
    for (const property of node.children ?? []) {
      const [key, value] = property.children ?? [];
      if (!key || !value) throw new ArtifactError('Invalid JSON property');
      if (keys.has(key.value as string)) throw new ArtifactError(`Duplicate JSON field: ${key.value}`);
      keys.add(key.value as string);
      inspect(key, bounds, depth + 1);
      inspect(value, bounds, depth + 1);
    }
  } else if (node.type === 'array') {
    for (const child of node.children ?? []) inspect(child, bounds, depth + 1);
  }
}

function checkInterval(start: number, end: number, duration: number): void {
  if (start > end || end > duration) {
    throw new ArtifactError('Interval outside declared source timeline');
  }
}

function summary(artifact: Artifact) {
  const sources: Record<string, number> = {};
  const channels: Record<string, number> = {};
  const categories: Record<string, number> = {};
  for (const finding of artifact.findings) {
    sources[finding.source] = (sources[finding.source] ?? 0) + 1;
    for (const item of finding.channel === 'both' ? ['audio', 'video'] : [finding.channel]) {
      channels[item] = (channels[item] ?? 0) + 1;
    }
    const label = finding.category === null ? 'unclassified' :
      `${finding.category}/${finding.subcategory}`;
    categories[label] = (categories[label] ?? 0) + 1;
  }
  return {
    schema_version: artifact.schema_version === 'community-artifact-2' ? 'community-summary-2' : 'community-summary-1', registry_version: 'mcf-1.1.0',
    submission_id: artifact.submission_id, kind: artifact.kind,
    work_ids: artifact.media.work_ids, release: artifact.media.release ?? null,
    duration_seconds: artifact.media.duration_seconds,
    release_version: artifact.provenance.release_version,
    scanner_behavior_version: artifact.provenance.scanner_behavior_version,
    total: artifact.findings.length,
    sources, channels, taxonomy: categories, coverage: artifact.coverage,
    ...(artifact.schema_version === 'community-artifact-2' ? {
      name: artifact.name, matching: artifact.matching, audio_association: artifact.audio_association,
      source_revision: artifact.source_revision, analyzed_audio: artifact.analyzed_audio,
      source_states: artifact.sources.map(({ source_id, origin, status }) => ({ source_id, origin, status })),
    } : {}),
  };
}

function equalJson(left: unknown, right: unknown): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length &&
      left.every((item, index) => equalJson(item, right[index]));
  }
  if (left !== null && right !== null && typeof left === 'object' && typeof right === 'object') {
    const first = left as Record<string, unknown>;
    const second = right as Record<string, unknown>;
    return Object.keys(first).length === Object.keys(second).length &&
      Object.keys(first).every((key) => Object.hasOwn(second, key) && equalJson(first[key], second[key]));
  }
  return left === right;
}

/** `stored` reads bytes the service already accepted, which may predate migration 0017. */
export async function parseArtifact(storedBytes: Uint8Array, bounds: ResourceBounds,
  compressed = false, stored = false): Promise<{ artifact: Artifact; storedBytes: Uint8Array;
    summary: ReturnType<typeof summary>; compressed: boolean; stored: boolean }> {
  if (Object.values(bounds).some((value) => !Number.isSafeInteger(value) || value <= 0)) {
    throw new ArtifactError('Resource bounds must be positive integers');
  }
  if (storedBytes.byteLength > (compressed ? bounds.compressedBytes : bounds.expandedBytes)) {
    throw new ArtifactError('Artifact exceeds configured byte limit');
  }
  let expanded = storedBytes;
  if (compressed) {
    try {
      const stream = new Blob([storedBytes as BlobPart]).stream()
        .pipeThrough(new DecompressionStream('gzip'));
      const reader = stream.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          length += value.byteLength;
          if (length > bounds.expandedBytes) {
            throw new ArtifactError('Expanded artifact exceeds configured byte limit');
          }
          chunks.push(value);
        }
      } finally {
        await reader.cancel();
      }
      expanded = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) {
        expanded.set(chunk, offset);
        offset += chunk.byteLength;
      }
    } catch (error) {
      if (error instanceof ArtifactError) throw error;
      throw new ArtifactError('Invalid gzip artifact');
    }
  }
  let value: unknown;
  try {
    const source = new TextDecoder('utf-8', { fatal: true }).decode(expanded);
    const errors: import('jsonc-parser').ParseError[] = [];
    const tree = parseTree(source, errors, { disallowComments: true, allowTrailingComma: false });
    if (!tree || errors.length) throw new ArtifactError('Invalid artifact JSON');
    inspect(tree, bounds);
    value = JSON.parse(source) as unknown;
  } catch (error) {
    if (error instanceof ArtifactError) throw error;
    throw new ArtifactError('Invalid artifact JSON or UTF-8');
  }
  const parsed = artifactSchema.safeParse(stored ? withoutArrProvenance(value) : value);
  if (!parsed.success) throw new ArtifactError(`Invalid community artifact: ${parsed.error.issues[0]?.path.join('.')}`);
  const artifact = parsed.data;
  if (artifact.schema_version === 'community-artifact-2') {
    try { validateV2(artifact, bounds.events); }
    catch (error) { throw new ArtifactError(error instanceof Error ? error.message : 'Invalid V2 evidence'); }
  }
  if (artifact.findings.length > bounds.events) throw new ArtifactError('Artifact exceeds configured event count');
  if (artifact.kind === 'derived' && ((!artifact.derivation?.parents.length &&
    !(artifact.schema_version === 'community-artifact-2' && artifact.sources.length && artifact.derivation)) ||
    artifact.media.timeline !== 'derived_target')) {
    throw new ArtifactError('Derived artifact requires parent lineage and target timeline');
  }
  if (artifact.kind !== 'derived' && artifact.derivation !== null) {
    throw new ArtifactError('Non-derived artifact cannot claim derived lineage');
  }
  if (artifact.kind === 'manual' && artifact.provenance.stages.length) {
    throw new ArtifactError('Manual artifact cannot claim scanner execution');
  }
  const eventIds = new Set<string>();
  for (const finding of artifact.findings) {
    checkInterval(finding.start_seconds, finding.end_seconds, artifact.media.duration_seconds);
    if (eventIds.has(finding.event_id)) throw new ArtifactError('Duplicate event ID');
    eventIds.add(finding.event_id);
    if ((finding.category === null) !== (finding.subcategory === null)) {
      throw new ArtifactError('Incomplete classification pair');
    }
    if (finding.subcategory !== null && taxonomyPairs.get(finding.subcategory) !== finding.category) {
      throw new ArtifactError('Unknown classification pair');
    }
    if (finding.source === 'derived' && artifact.kind !== 'derived') {
      throw new ArtifactError('Derived finding without derived artifact');
    }
    if (finding.parent_event_id !== null && artifact.kind !== 'derived') {
      throw new ArtifactError('Parent event outside derived artifact');
    }
  }
  for (const record of [...artifact.coverage, ...artifact.provenance.stages]) {
    for (const interval of record.intervals) {
      checkInterval(interval.start_seconds, interval.end_seconds, artifact.media.duration_seconds);
    }
  }
  for (const stage of artifact.provenance.stages) {
    const settings = artifact.schema_version === 'community-artifact-2' ? allowedSettingsV2 : allowedSettings;
    if (Object.keys(stage.settings).some((key) => !settings.has(key))) {
      throw new ArtifactError('Unsupported effective setting');
    }
  }
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(artifact.created_at)) {
    throw new ArtifactError('Creation time must be UTC seconds');
  }
  return { artifact, storedBytes, summary: summary(artifact), compressed, stored };
}

export async function parseManifest(storedBytes: Uint8Array, artifact: Awaited<ReturnType<
  typeof parseArtifact>>, bounds: ResourceBounds) {
  if (!artifact.compressed) throw new ArtifactError('Manifest requires gzip JSON artifact bytes');
  if (storedBytes.byteLength > bounds.expandedBytes) {
    throw new ArtifactError('Manifest exceeds configured byte limit');
  }
  let value: unknown;
  try {
    const source = new TextDecoder('utf-8', { fatal: true }).decode(storedBytes);
    const errors: import('jsonc-parser').ParseError[] = [];
    const tree = parseTree(source, errors, { disallowComments: true, allowTrailingComma: false });
    if (!tree || errors.length) throw new ArtifactError('Invalid manifest JSON');
    inspect(tree, bounds);
    value = JSON.parse(source) as unknown;
  } catch (error) {
    if (error instanceof ArtifactError) throw error;
    throw new ArtifactError('Invalid manifest JSON or UTF-8');
  }
  const parsed = manifestSchema.safeParse(value);
  if (!parsed.success) throw new ArtifactError(`Invalid community manifest: ${parsed.error.issues[0]?.path.join('.')}`);
  const manifest = parsed.data;
  const digest = await crypto.subtle.digest('SHA-256', artifact.storedBytes as BufferSource);
  const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  if (manifest.artifact_schema_version !== artifact.artifact.schema_version ||
    manifest.artifact_digest !== hash || manifest.submission_id !== artifact.artifact.submission_id ||
    manifest.contributor_id !== artifact.artifact.author.contributor_id ||
    manifest.key_id !== artifact.artifact.author.key_id ||
    !equalJson(artifact.stored ? withoutArrProvenance(manifest.summary) : manifest.summary, artifact.summary)) {
    throw new ArtifactError('Manifest does not match artifact bytes, identity, or summary');
  }
  return { manifest, storedBytes };
}