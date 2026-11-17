import type { Artifact } from './schema';

const modelToken = /^[A-Za-z0-9](?:[A-Za-z0-9._:+@-]{0,63}|[A-Za-z0-9._+@-]{0,63}\/[A-Za-z0-9][A-Za-z0-9._+@-]{0,63})(?![\s\S])/;
const languageDetectors = new Set(['alt_profanity_check', 'better_profanity', 'glin_profanity']);

export function contributionMetadata(artifact: Artifact) {
  if (artifact.schema_version !== 'community-artifact-2' || artifact.manual_filter_tracking === undefined)
    return {};
  const findings = artifact.findings;
  const provenance = [artifact.provenance, ...artifact.sources.map(source => source.provenance)];
  const models = new Map<string, { capability: string; model_id: string; model_revision: string | null }>();
  function addModel(capability: string, model_id: string, revision: string | null) {
    const model_revision = revision && modelToken.test(revision) ? revision : null;
    models.set([capability, model_id, model_revision ?? ''].join('\0'), { capability, model_id, model_revision });
  }
  for (const recorded of provenance) {
    for (const stage of recorded.stages) {
      if (stage.state !== 'complete' && stage.state !== 'partial') continue;
      const capability = /^visual_qwen:scene_[0-9]+(?![\s\S])/.test(stage.capability) ? 'visual_qwen' : stage.capability;
      if (stage.model_id && modelToken.test(capability) && modelToken.test(stage.model_id))
        addModel(capability, stage.model_id, stage.model_revision);
      const detector = stage.settings.audio_filter;
      if (stage.capability === 'audio_scan' && typeof detector === 'string' && languageDetectors.has(detector))
        addModel('audio_filter', detector, null);
    }
  }
  function count(field: 'added' | 'edited'): number | null {
    let total = 0;
    for (const finding of findings) {
      const state = finding.manual?.[field];
      if (state == null) return null;
      total += Number(state);
    }
    return total;
  }
  return {
    models: [...models.keys()].sort().slice(0, 16).map(key => models.get(key)!),
    manual_filter_counts: { added: count('added'), edited: count('edited') },
  };
}
