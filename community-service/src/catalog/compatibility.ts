import {
  BANDS, DURATION_TOLERANCE_SECONDS, FULL_ALGORITHM, POLICY_VERSION, SAMPLED_ALGORITHM,
  matchEvidenceSchema, type Band, type ChannelCompatibility, type Compatibility,
  type EpisodeNumber, type Identifier, type MatchEvidence, type MatchEvidenceInput,
} from './compatibility-inputs';
import { PCM_AUDIO_ALGORITHM, compareStreams } from './stream-compatibility';

function ids(left: Identifier[], right: Identifier[]): [boolean, boolean] {
  const first = new Map(left.map(item => [`${item.provider}:${item.entity_type}`, item.value]));
  const second = new Map(right.map(item => [`${item.provider}:${item.entity_type}`, item.value]));
  const shared = [...first.keys()].filter(key => second.has(key));
  return [shared.some(key => first.get(key) === second.get(key)), shared.some(key => first.get(key) !== second.get(key))];
}

function groups(numbers: EpisodeNumber[]) {
  const output = new Map<string, string[]>();
  for (const item of numbers) {
    const values = output.get(item.scheme) ?? [];
    values.push(`${item.season}:${item.number}`); output.set(item.scheme, values);
  }
  return output;
}

function work(left: MatchEvidence['media'], right: MatchEvidence['media']): [boolean, string[], string[]] {
  if (left.entity_type !== right.entity_type) return [false, ['entity_type'], []];
  let [matched, conflict] = ids(left.identifiers, right.identifiers);
  const conflicts = conflict ? ['work_identity'] : [];
  const missing: string[] = [];
  if (left.entity_type === 'episode') {
    const [seriesMatch, seriesConflict] = ids(left.series_identifiers, right.series_identifiers);
    if (seriesConflict) conflicts.push('series_identity');
    const numbers = groups(left.episode_numbers), others = groups(right.episode_numbers);
    const shared = [...numbers.keys()].filter(key => others.has(key));
    if (shared.some(key => JSON.stringify([...numbers.get(key)!].sort()) !== JSON.stringify([...others.get(key)!].sort())))
      conflicts.push('episode_numbering');
    if (!shared.length) missing.push('episode_numbering');
    matched ||= seriesMatch && shared.length > 0 && !conflicts.includes('episode_numbering');
    const spans = groups(left.episode_span.length ? left.episode_span : left.episode_numbers);
    const otherSpans = groups(right.episode_span.length ? right.episode_span : right.episode_numbers);
    const sharedSpans = [...spans.keys()].filter(key => otherSpans.has(key));
    if (sharedSpans.some(key => JSON.stringify(spans.get(key)) !== JSON.stringify(otherSpans.get(key)))) conflicts.push('episode_span');
    if (!sharedSpans.length) missing.push('episode_span');
  }
  const identified = [left, right].every(item => item.identifiers.length || (item.series_identifiers.length && item.episode_numbers.length));
  if (!identified) missing.push('work_identity');
  else if (!matched && !conflicts.length) missing.push('work_link');
  return [matched, conflicts, missing];
}

const unique = (items: string[]) => [...new Set(items)].sort();
function channel(band: Band, reasons: string[], conflicts: string[], missing: string[]): ChannelCompatibility {
  return { band, reasons: unique(reasons), conflicts: unique(conflicts), missing: unique(missing),
    strong_eligible: band === 'exact_file' || band === 'strong_fingerprint' };
}

export function classify(target: MatchEvidence, candidate: MatchEvidence): Compatibility {
  target = matchEvidenceSchema.parse(target); candidate = matchEvidenceSchema.parse(candidate);
  const [matched, conflicts, missing] = work(target.media, candidate.media);
  const reasons = matched ? ['work_match'] : [];
  const left = target.media.release, right = candidate.media.release;
  if (left.edition !== null && right.edition !== null && left.edition !== right.edition) conflicts.push('edition');
  if (left.duration?.source === 'file_probe' && right.duration?.source === 'file_probe' &&
    Math.abs(left.duration.seconds - right.duration.seconds) > DURATION_TOLERANCE_SECONDS) conflicts.push('measured_duration');
  const measured = [left, right].every(item => item.duration?.source === 'file_probe' &&
    (item.duration.precision_seconds === null || item.duration.precision_seconds <= DURATION_TOLERANCE_SECONDS));
  if (measured && !conflicts.includes('measured_duration')) reasons.push('measured_duration_match');
  if (!measured) missing.push('measured_duration');
  const first = new Map(target.fingerprints.map(item => [item.algorithm, item]));
  const second = new Map(candidate.fingerprints.map(item => [item.algorithm, item]));
  const equal = new Set<string>(), different = new Set<string>();
  for (const algorithm of [FULL_ALGORITHM, SAMPLED_ALGORITHM]) {
    const firstFingerprint = first.get(algorithm), secondFingerprint = second.get(algorithm);
    if (!firstFingerprint || !secondFingerprint) continue;
    if (firstFingerprint.digest !== secondFingerprint.digest) different.add(algorithm);
    else if (firstFingerprint.size_bytes !== secondFingerprint.size_bytes) conflicts.push('fingerprint_size');
    else equal.add(algorithm);
  }
  if (different.has(FULL_ALGORITHM)) { equal.delete(SAMPLED_ALGORITHM); reasons.push('full_digest_differs'); }
  if (equal.has(FULL_ALGORITHM) && different.has(SAMPLED_ALGORITHM)) conflicts.push('fingerprint_evidence');
  if (equal.size) reasons.push(equal.has(FULL_ALGORITHM) ? 'full_digest_match' : 'sampled_digest_match');
  else missing.push('matching_fingerprint');
  if ([...target.fingerprints, ...candidate.fingerprints].some(item => ![FULL_ALGORITHM, SAMPLED_ALGORITHM].includes(item.algorithm)))
    reasons.push('unsupported_fingerprint_ignored');
  const releaseMatch = left.scene_name && left.group && left.scene_name === right.scene_name && left.group === right.group;
  if (releaseMatch) reasons.push('release_name_match'); else missing.push('release_name');
  if (target.media.entity_type === 'series') missing.push('physical_episode_or_movie');
  let band: Band;
  if (conflicts.length) band = 'incompatible';
  else if (!matched) band = 'title_only';
  else if (!measured || ['episode_numbering', 'episode_span', 'physical_episode_or_movie'].some(key => missing.includes(key))) band = 'possible_cut';
  else if (equal.has(FULL_ALGORITHM)) band = 'exact_file';
  else if (equal.has(SAMPLED_ALGORITHM)) band = 'strong_fingerprint';
  else if (releaseMatch) band = 'release_match';
  else band = 'possible_cut';
  const audioConflicts = [...conflicts];
  if (left.audio?.language && right.audio?.language && left.audio.language !== right.audio.language) audioConflicts.push('audio_language');
  if (left.audio && right.audio && left.audio.role !== 'unknown' && right.audio.role !== 'unknown' && left.audio.role !== right.audio.role)
    audioConflicts.push('audio_role');
  const audioMissing = [...missing, 'analyzed_audio_stream'];
  if ([left, right].some(item => !item.audio?.language || item.audio.role === 'unknown')) audioMissing.push('audio_track_metadata');
  const audioBand = audioConflicts.length ? 'incompatible' : band === 'title_only' ? 'title_only' : 'possible_cut';
  const contextComplete = matched && measured &&
    !['episode_numbering', 'episode_span', 'physical_episode_or_movie'].some(key => missing.includes(key));
  const channels = (['video', 'audio'] as const).map(name => {
    const baseBand = name === 'video' ? band : audioBand;
    const channelConflicts = name === 'video' ? conflicts : audioConflicts;
    const channelMissing = name === 'video' ? missing : audioMissing;
    const firstStream = target[`${name}_stream`], secondStream = candidate[`${name}_stream`];
    if (!firstStream && !secondStream) return channel(baseBand, reasons, channelConflicts, channelMissing);
    const stream = compareStreams(name, firstStream, secondStream);
    const finalConflicts = [...channelConflicts, ...stream.conflicts];
    let finalMissing = channelMissing.filter(key => key !== 'analyzed_audio_stream');
    if (stream.equal) {
      finalMissing = finalMissing.filter(key => key !== 'matching_fingerprint');
      if (name === 'audio' && firstStream?.algorithm === PCM_AUDIO_ALGORITHM)
        finalMissing = finalMissing.filter(key => key !== 'audio_track_metadata');
    }
    finalMissing.push(...stream.missing);
    const streamReady = !stream.missing.length && !finalMissing.includes('audio_track_metadata');
    let finalBand: Band;
    if (finalConflicts.length) finalBand = 'incompatible';
    else if (!matched) finalBand = 'title_only';
    else if (!contextComplete) finalBand = 'possible_cut';
    else if (stream.equal && streamReady) finalBand = equal.has(FULL_ALGORITHM) ? 'exact_file' : 'strong_fingerprint';
    else finalBand = 'possible_cut';
    return channel(finalBand, [...reasons, ...stream.reasons], finalConflicts, finalMissing);
  });
  return { policy_version: POLICY_VERSION, video: channels[0]!, audio: channels[1]!, selected: false };
}

export function classifyCandidates(target: MatchEvidence, candidates: Iterable<[string, MatchEvidence]>, limit: number): [string, Compatibility][] {
  if (!Number.isSafeInteger(limit) || limit < 1) throw new Error('An explicit positive candidate limit is required');
  const results: [string, Compatibility][] = [], seen = new Set<string>();
  for (const [identifier, candidate] of candidates) {
    if (results.length >= limit) throw new Error('Candidate limit exceeded');
    if (!/^[a-zA-Z0-9_-]{1,128}$/.test(identifier) || seen.has(identifier)) throw new Error('Invalid candidate ID');
    seen.add(identifier); results.push([identifier, classify(target, candidate)]);
  }
  return results.sort(([leftId, left], [rightId, right]) => BANDS.indexOf(left.video.band) - BANDS.indexOf(right.video.band) ||
    BANDS.indexOf(left.audio.band) - BANDS.indexOf(right.audio.band) || (leftId < rightId ? -1 : leftId > rightId ? 1 : 0));
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([key, item]) => [key, canonical(item)]));
  return value;
}

export function compatibilityCacheKey(target: MatchEvidenceInput, candidate: MatchEvidenceInput, policyVersion = POLICY_VERSION): string {
  return JSON.stringify(canonical([policyVersion, matchEvidenceSchema.parse(target), matchEvidenceSchema.parse(candidate)]));
}
