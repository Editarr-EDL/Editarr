import { z } from 'zod';
import { matchEvidenceSchema } from '../catalog/compatibility-inputs';

const text = z.string().min(1), nullableText = text.nullable(), finite = z.number().finite();
const interval = z.strictObject({ start_seconds: finite.nonnegative(), end_seconds: finite.nonnegative() });
const state = z.enum(['complete', 'partial', 'failed', 'disabled', 'unsupported', 'unknown']);
const channel = z.enum(['audio', 'video', 'both']);
const parent = z.strictObject({ submission_id: text, digest: z.string().regex(/^[0-9a-f]{64}$/) });
const stage = z.strictObject({
  execution_id: text, capability: text, state, model_id: nullableText,
  model_revision: nullableText, model_digest: nullableText,
  settings: z.record(z.string(), z.union([text, finite, z.boolean(), z.null()])),
  intervals: z.array(interval), sampling_seconds: finite.positive().nullable().optional(),
});
const provenance = z.strictObject({
  release_version: nullableText, scanner_behavior_version: nullableText,
  stages: z.array(stage), build_revision: nullableText.optional(),
});
const coverage = z.strictObject({ channel, capability: text, state, intervals: z.array(interval) });
const finding = z.strictObject({
  event_id: text, start_seconds: finite.nonnegative(), end_seconds: finite.nonnegative(),
  source: z.enum(['audio', 'visual', 'manual', 'derived']), channel,
  category: nullableText, subcategory: nullableText,
  severity: z.enum(['low', 'medium', 'high']).nullable(),
  confidence: finite.min(0).max(1).nullable(), action: z.enum(['skip', 'mute']).nullable(),
  note: nullableText, parent_event_id: nullableText,
});
const v1 = z.strictObject({
  schema_version: z.literal('community-artifact-1'), submission_id: text,
  kind: z.enum(['scan', 'manual', 'derived']), created_at: text,
  author: z.strictObject({ contributor_id: text, key_id: text }), sharing_terms: nullableText,
  source_references: z.array(parent).optional(),
  media: z.strictObject({
    duration_seconds: finite.positive(), timeline: z.enum(['source_file', 'derived_target']),
    time_unit: z.literal('seconds'), work_ids: z.array(z.strictObject({ provider: text, entity_type: text, value: text })),
    release: nullableText.optional(), fingerprint_algorithm: nullableText.optional(),
    fingerprint_digest: nullableText.optional(), audio_track: nullableText.optional(), audio_language: nullableText.optional(),
  }),
  provenance, coverage: z.array(coverage), findings: z.array(finding),
  derivation: z.strictObject({ parents: z.array(parent), transform: nullableText }).nullable(),
  supersedes: nullableText.optional(),
});
const stageV2 = stage.extend({
  release_version: nullableText.optional(), scanner_behavior_version: nullableText.optional(),
  build_revision: nullableText.optional(), quantization: nullableText.optional(),
});
const provenanceV2 = provenance.extend({ stages: z.array(stageV2) });
const v2 = v1.extend({
  schema_version: z.literal('community-artifact-2'), name: text,
  source_revision: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER), matching: matchEvidenceSchema,
  audio_association: z.enum(['none', 'unknown', 'identified']), audio_source_id: nullableText,
  analyzed_audio: z.strictObject({
    provenance: z.literal('analyzed'), language: z.string().nullable(), description: z.string().nullable(),
    role: z.enum(['main', 'commentary', 'descriptive', 'unknown']), codec: z.string().nullable(),
    channels: z.number().int().min(1).max(256).nullable(), stream_start_seconds: finite.nullable(),
    stream_duration_seconds: finite.nonnegative().nullable(), time_base: z.string().nullable(),
  }).nullable(),
  sources: z.array(z.strictObject({
    source_id: z.string().regex(/^source-[1-9][0-9]*$/), origin: z.enum(['generated', 'manual', 'imported']),
    status: z.enum(['complete', 'failed', 'cancelled']),
    created_at: z.string(), revision: z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER), duration_seconds: finite.positive().nullable(),
    provenance: provenanceV2, coverage: z.array(coverage), reference: parent.nullable(), author_id: z.string().nullable(),
  })),
  retiming: z.array(z.strictObject({
    source_id: z.string(), source_bounds: interval.nullable(), target_bounds: interval,
    mapping: z.enum(['affine', 'unknown']), applied: z.literal(true),
  })),
  provenance: provenanceV2,
  findings: z.array(finding.extend({ source_snapshot_id: nullableText.optional() })),
});
export const artifactSchema = z.discriminatedUnion('schema_version', [v1, v2]);
export type Artifact = z.infer<typeof artifactSchema>;
export type ArtifactV2 = z.infer<typeof v2>;
export const manifestSchema = z.strictObject({
  schema_version: z.literal('community-manifest-1'), artifact_format: z.literal('gzip-json'),
  artifact_schema_version: z.enum(['community-artifact-1', 'community-artifact-2']),
  artifact_digest: z.string().regex(/^[0-9a-f]{64}$/),
  submission_id: text, contributor_id: text, key_id: text, summary: z.record(z.string(), z.unknown()),
});

export const allowedSettings = new Set(['frame_interval_seconds', 'speed_up', 'audio_model',
  'video_model', 'qwen_model', 'credits_model']);
export const allowedSettingsV2 = new Set([...allowedSettings,
  'scene_detection_threshold', 'coarse_scan_enabled', 'coarse_scan_interval_seconds',
  'qwen_summaries_per_segment', 'qwen_segment_join_gap_seconds', 'qwen_model_requested',
  'audio_filter', 'whisper_model', 'detector_version']);

export function validateV2(artifact: ArtifactV2, sourceLimit: number): void {
  const fail = (message: string): never => { throw new Error(message); };
  const identity = artifact.matching.media;
  const workIds = [...identity.identifiers, ...identity.series_identifiers].map(({ provider, entity_type, value }) => ({ provider, entity_type, value }));
  if (JSON.stringify(workIds) !== JSON.stringify(artifact.media.work_ids)) fail('V2 work identity disagrees with media');
  if (identity.release.duration && identity.release.duration.seconds !== artifact.media.duration_seconds) fail('V2 matching duration disagrees with timeline');
  if (artifact.sources.length > sourceLimit || artifact.retiming.length > sourceLimit) fail('V2 source lineage exceeds configured bound');
  const sources = new Map(artifact.sources.map(item => [item.source_id, item]));
  if (sources.size !== artifact.sources.length) fail('Duplicate V2 source snapshot');
  for (const source of artifact.sources) {
    if (source.origin !== 'generated' && source.provenance.stages.length) fail('Non-generated source cannot claim scanner execution');
    for (const record of [...source.provenance.stages, ...source.coverage]) {
      for (const interval of record.intervals) {
        if (source.duration_seconds === null || interval.start_seconds > interval.end_seconds || interval.end_seconds > source.duration_seconds)
          fail('Source coverage outside declared timeline');
      }
    }
    if (source.provenance.stages.some(item => Object.keys(item.settings).some(key => !allowedSettingsV2.has(key)))) fail('Unsupported source effective setting');
  }
  if (artifact.audio_association === 'identified') {
    const source = sources.get(artifact.audio_source_id ?? '');
    if (!source || source.origin !== 'generated' || !artifact.analyzed_audio) fail('Identified audio requires its generated source snapshot');
    if (artifact.matching.audio_stream && artifact.matching.audio_stream.provenance !== 'analyzed') fail('Identified audio must be actually analyzed');
    const metadata = artifact.analyzed_audio!, track = identity.release.audio;
    if (!track || track.language !== metadata.language || track.role !== metadata.role || track.codec !== metadata.codec || track.channels !== metadata.channels)
      fail('Analyzed audio metadata disagrees with matching evidence');
    if (artifact.matching.audio_stream && artifact.matching.audio_stream.description !== metadata.description)
      fail('Analyzed audio description disagrees with matching evidence');
  } else if (artifact.matching.audio_stream || identity.release.audio || artifact.audio_source_id || artifact.analyzed_audio)
    fail('Unknown audio cannot claim an identified stream or track');
  if (artifact.audio_association === 'none' && artifact.findings.some(item => ['audio', 'both'].includes(item.channel))) fail('Audio findings require explicit association');
  for (const finding of artifact.findings)
    if (finding.source_snapshot_id && !sources.has(finding.source_snapshot_id)) fail('Finding refers to unknown source snapshot');
  const seen = new Set<string>();
  if (artifact.kind !== 'derived' && artifact.retiming.length) fail('Only derived target timelines can contain retiming');
  for (const record of artifact.retiming) {
    if (!sources.has(record.source_id) || seen.has(record.source_id)) fail('Retiming requires one record per known source');
    seen.add(record.source_id);
    for (const interval of [record.source_bounds, record.target_bounds])
      if (interval && interval.end_seconds <= interval.start_seconds) fail('Invalid retiming bounds');
    if ((record.mapping === 'affine') !== (record.source_bounds !== null)) fail('Unknown retiming cannot fabricate source bounds');
    if (record.target_bounds.end_seconds > artifact.media.duration_seconds) fail('Retiming target exceeds artifact timeline');
    const sourceDuration = sources.get(record.source_id)!.duration_seconds;
    if (record.source_bounds && sourceDuration !== null && record.source_bounds.end_seconds > sourceDuration) fail('Retiming source exceeds source timeline');
  }
  if (artifact.kind === 'derived' && seen.size !== sources.size) fail('Derived artifact requires explicit retiming state for every source');
  const declared = [...artifact.source_references ?? [], ...artifact.derivation?.parents ?? []];
  if (artifact.sources.some(source => source.reference && !declared.some(parent => parent.submission_id === source.reference!.submission_id && parent.digest === source.reference!.digest)))
    fail('Source reference is missing from declared lineage');
}