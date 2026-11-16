import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { matchEvidenceSchema, type MatchEvidence } from './compatibility-inputs';
import { boundedJson, positive, timestamp, token } from './records';
import { episodeWorkStatements, workStatements } from './works';

export async function releaseStatements(db: SqlDatabase, releaseId: string, input: MatchEvidence,
  now: number, limits: { maxJsonBytes: number; maxReadQueries: number; maxStatements: number }) {
  token(releaseId);
  timestamp(now);
  const evidence = matchEvidenceSchema.parse(input);
  if (evidence.media.identifiers.length + evidence.media.series_identifiers.length > positive(limits.maxReadQueries))
    throw new Error('catalog_read_limit');
  const statements: SqlStatement[] = [];
  const primaryWorks: string[] = [], seriesWorks: string[] = [];
  for (const [identifiers, destination] of [
    [evidence.media.identifiers, primaryWorks], [evidence.media.series_identifiers, seriesWorks],
  ] as const) {
    for (const item of identifiers) {
      const work = await workStatements(db, item, now);
      statements.push(...work.statements);
      if (!destination.includes(work.workId)) destination.push(work.workId);
    }
  }
  if (!primaryWorks.length) {
    const number = evidence.media.episode_numbers[0] ?? evidence.media.episode_span[0];
    if (evidence.media.entity_type !== 'episode' || !seriesWorks.length || !number)
      throw new Error('missing_work_association');
    const work = await episodeWorkStatements(db, seriesWorks[0], number, now);
    statements.push(...work.statements);
    primaryWorks.push(work.workId);
  }
  const release = evidence.media.release;
  statements.push(db.prepare(`INSERT INTO releases
    (release_id, work_id, evidence_json, scene_name, edition, duration_seconds, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(releaseId, primaryWorks[0], boundedJson(evidence, limits.maxJsonBytes),
    release.scene_name, release.edition, release.duration?.seconds ?? null, now));
  for (const workId of primaryWorks) statements.push(db.prepare(`INSERT INTO release_work_associations
    (release_id, work_id) VALUES (?, ?)`).bind(releaseId, workId));
  for (const seriesWorkId of seriesWorks) {
    for (const [role, numbers] of [['number', evidence.media.episode_numbers], ['span', evidence.media.episode_span]] as const) {
      numbers.forEach((number, position) => statements.push(db.prepare(`INSERT INTO release_episodes
        (release_id, series_work_id, scheme, season, episode_number, role, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(releaseId, seriesWorkId, number.scheme,
        number.season ?? -1, number.number, role, position)));
    }
  }
  for (const item of evidence.fingerprints) statements.push(db.prepare(`INSERT INTO release_fingerprints
    (release_id, channel, algorithm, digest, size_bytes) VALUES (?, 'file', ?, ?, ?)`)
    .bind(releaseId, item.algorithm, item.digest, item.size_bytes));
  for (const [channel, item] of [['audio', evidence.audio_stream], ['video', evidence.video_stream]] as const) {
    if (item) statements.push(db.prepare(`INSERT INTO release_fingerprints
      (release_id, channel, algorithm, digest) VALUES (?, ?, ?, ?)`).bind(releaseId, channel, item.algorithm, item.digest));
  }
  if (statements.length > positive(limits.maxStatements)) throw new Error('catalog_statement_limit');
  return { releaseId, workId: primaryWorks[0], statements };
}