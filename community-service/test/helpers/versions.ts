import { readFileSync, readdirSync } from 'node:fs';
import { env } from './environment';
import { DEFAULT_MASTER_POLICY } from '../../src/filters/policy';
import type { VersionAcceptanceInput } from '../../src/versions/acceptance-service';
import type { EvidenceKey } from '../../src/versions/keys';

export const db = env.CATALOG_DB;
export const now = 1_700_000_030;
export const policy = DEFAULT_MASTER_POLICY;
const directory = new URL('../../migrations/', import.meta.url);

/** Apply every service migration in order and register plain contributors for acceptance rows. */
export async function setupVersionCatalog(contributors: string[] = ['c1', 'c2', 'c3', 'c4']) {
  for (const name of readdirSync(directory).filter(name => /^\d{4}_.+\.sql$/.test(name)).sort())
    await db.exec(readFileSync(new URL(name, directory), 'utf8'));
  await addContributors(contributors);
}

export async function addContributors(ids: string[]) {
  for (const id of ids) await db.prepare(`INSERT INTO contributors (contributor_id, discord_user_id, display_name, display_name_key, created_at)
    VALUES (?, ?, ?, ?, 1)`).bind(id, `discord-${id}`, `Name ${id}`, `name ${id}`).run();
}

export const hex = (seed: string, length = 64) => seed.repeat(length).slice(0, length);
export const keys = {
  full: (seed = 'a'): EvidenceKey => ({ type: 'sha256_full', value: hex(seed), sizeBytes: 1000 }),
  sampled: (seed = 'b'): EvidenceKey => ({ type: 'sha256_sampled', value: hex(seed), sizeBytes: 1000 }),
  md5: (seed = 'c'): EvidenceKey => ({ type: 'md5_quick', value: hex(seed, 32), sizeBytes: 1000 }),
  audio: (seed = 'd'): EvidenceKey => ({ type: 'audio_stream', algorithm: 'editarr-community-audio-sha256-sampled-1', value: hex(seed) }),
  video: (seed = 'e'): EvidenceKey => ({ type: 'video_stream', algorithm: 'editarr-community-video-sha256-sampled-1', value: hex(seed) }),
};

let sequence = 0;
/** A movie submission for TMDB 603 with a Theatrical alias unless overridden. */
export function submission(overrides: Partial<VersionAcceptanceInput> = {}): VersionAcceptanceInput {
  sequence++;
  return { submissionId: `sub_${sequence}`, contributorId: 'c1',
    work: { kind: 'movie', tmdb: '603', tvdb: null, season: null, episode: null, lastEpisode: null, title: 'The Matrix', year: 1999 },
    version: { choice: 'alias', aliasId: 'theatrical' }, durationMs: 8_160_000, matchKeys: [], ...overrides };
}

let ids = 0;
/** Deterministic, ASCII-ordered service IDs for assertions. */
export const newId = (prefix: string) => `${prefix}_${String(++ids).padStart(4, '0')}`;
