import { z } from 'zod';

export const AUDIO_ALGORITHM = 'editarr-community-audio-sha256-sampled-1';
export const VIDEO_ALGORITHM = 'editarr-community-video-sha256-sampled-1';
const maximum = (1n << 63n) - 1n;

function rational(value: string): [bigint, bigint] {
  if (!/^-?[0-9]{1,19}(?:\/[0-9]{1,19})?$/.test(value)) throw new Error('Invalid rational timing');
  const [numerator, denominator = '1'] = value.split('/');
  const first = BigInt(numerator!), second = BigInt(denominator);
  if (first < -maximum || first > maximum || second <= 0n || second > maximum)
    throw new Error('Timing components exceed bounds');
  return [first, second];
}

const seconds = z.string().refine(value => {
  try { rational(value); return true; } catch { return false; }
});
export const streamEvidenceSchema = z.strictObject({
  algorithm: z.string().regex(/^[a-zA-Z0-9_-]{1,128}$/),
  digest: z.string().regex(/^[a-f0-9]{64}$/),
  playback_start: seconds.nullable().default(null),
  duration: z.string().refine(value => {
    try { return rational(value)[0] > 0n; } catch { return false; }
  }).nullable().default(null),
  provenance: z.enum(['unknown', 'selected', 'analyzed']).default('unknown'),
  description: z.string().refine(value => Array.from(value).length <= 512).nullable().default(null),
});
export type StreamEvidence = z.infer<typeof streamEvidenceSchema>;

function sameSeconds(left: string, right: string): boolean {
  const [first, firstScale] = rational(left), [second, secondScale] = rational(right);
  return first * secondScale === second * firstScale;
}

export function compareStreams(channel: 'video' | 'audio', left: StreamEvidence | null, right: StreamEvidence | null) {
  const reasons: string[] = [], conflicts: string[] = [], missing: string[] = [];
  const algorithm = channel === 'audio' ? AUDIO_ALGORITHM : VIDEO_ALGORITHM;
  const supported = left !== null && right !== null && left.algorithm === algorithm && right.algorithm === algorithm;
  const equal = Boolean(supported && left!.digest === right!.digest);
  if (equal) reasons.push(`${channel}_stream_digest_match`);
  else {
    missing.push(`${channel}_stream_fingerprint`);
    if (supported) reasons.push(`${channel}_stream_digest_differs`);
  }
  if ([left, right].some(item => item !== null && item.algorithm !== algorithm))
    reasons.push('unsupported_stream_fingerprint_ignored');
  let timed = true;
  for (const field of ['playback_start', 'duration'] as const) {
    const first = left?.[field], second = right?.[field];
    if (first === null || first === undefined || second === null || second === undefined) timed = false;
    else if (!sameSeconds(first, second)) conflicts.push(`${channel}_stream_${field}`);
  }
  if (timed && !conflicts.length) reasons.push(`${channel}_stream_timing_match`);
  if (!timed) missing.push(`${channel}_stream_timing`);
  if (channel === 'audio' && (!left || left.provenance === 'unknown' || !right || right.provenance !== 'analyzed'))
    missing.push('analyzed_audio_stream');
  return { equal, reasons, conflicts, missing };
}