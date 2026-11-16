export interface Stage {
  stage: string;
  execution_id: string;
  status: string;
  model_id: string | null;
  model_revision: string | null;
  settings_json: string;
  intervals: { start_seconds: number; end_seconds: number }[];
  release_version: string;
  scanner_behavior_version: string;
  build_revision: string | null;
  model_digest: string | null;
  quantization: string | null;
  sampling_seconds: number | null;
  source: string;
}

export interface Artifact {
  schema_version: string;
  submission_id: string;
  kind: string;
  work_id: string;
  release: string;
  scanner_version: string;
  parent_digest: string | null;
  stages: Stage[];
  events: Event[];
}

export interface Event {
  event_id: string;
  start_seconds: number;
  end_seconds: number;
  category: string;
  subcategory: string;
  severity: string;
  source: string;
  note: string;
}

export function fixtures(): Record<string, Artifact> {
  const stage: Stage = {
    stage: 'visual', execution_id: 'attempt-a', status: 'complete', model_id: 'nudenet',
    model_revision: null, settings_json: '{"coarse_scan_enabled":false}',
    intervals: [{ start_seconds: 0, end_seconds: 5400 }], release_version: '0.1.0',
    scanner_behavior_version: 'scanner-1', build_revision: null, model_digest: null,
    quantization: null, sampling_seconds: null, source: 'generated',
  };
  const common = {
    schema_version: 'experiment-1', submission_id: 'synthetic-0001', kind: 'generated',
    work_id: 'movie:synthetic', release: 'test-1080p', scanner_version: 'scanner-1',
    parent_digest: null, stages: [stage], events: [],
  } satisfies Artifact;
  const event = (index: number): Event => ({
    event_id: `event-${String(index).padStart(6, '0')}`,
    start_seconds: index * 7, end_seconds: index * 7 + 2,
    category: 'language', subcategory: 'swearing', severity: 'medium', source: 'audio',
    note: `Synthetic context for event ${index % 17}.`,
  });
  const events = (count: number) => Array.from({ length: count }, (_, index) => event(index));
  const manualEvents = events(5).map((item) => ({
    ...item, source: 'manual', note: 'Reviewed synthetic note',
  }));
  return {
    small: { ...common, events: events(12) },
    large: { ...common, events: events(2500) },
    zero: { ...common },
    partial: { ...common, stages: [stage,
      { ...stage, stage: 'visual_qwen:scene_0001', status: 'partial',
        model_id: 'qwen3-vl-4b-instruct-4bit', settings_json: '{}', intervals: [] },
      { ...stage, stage: 'audio_scan', execution_id: 'attempt-b', model_id: 'whisper',
        settings_json: '{"audio_model":"whisper"}', intervals: [] },
      { ...stage, stage: 'visual_qwen:scene_0002', execution_id: 'attempt-b',
        status: 'partial', model_id: null, settings_json: '{"skip_reason":"cached_result"}',
        intervals: [] },
    ], events: events(25) },
    manual: { ...common, kind: 'manual', stages: [], events: manualEvents },
    derived: { ...common, kind: 'derived', stages: [], parent_digest: 'a'.repeat(64),
      events: events(20).map((item) => ({ ...item, source: 'derived' })) },
  };
}