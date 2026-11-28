import { beforeAll, describe, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { DEFAULT_MASTER_POLICY } from '../src/filters/policy';
import { importExternalSeed } from '../src/seeds/service';
import { db, now, setupVersionCatalog } from './helpers/versions';

const settings = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }) };
const cue = { startMs: 1000, endMs: 3000, category: 'language', subcategory: 'swearing', severity: 'medium', action: 'mute', sourceCueIndex: 1 };
let jwt: string, resolver: JWTVerifyGetKey;

beforeAll(async () => {
  await setupVersionCatalog([]);
  await importExternalSeed(db, { schemaVersion: 'editarr-community-seed-1', source: 'VideoSkip', records: [{
    sourceRecordId: 'videoskip:1', work: { kind: 'movie', tmdb: '603', tvdb: null, title: 'The Matrix', year: 1999, season: null, episode: null },
    sourceRuntimeMs: null, provenance: { attribution: { source_url: 'https://example.test/archive' }, sync: null, match: { imdb: 'tt0133093' } }, filters: [cue] }] },
  { service: 'https://example.test', policy: DEFAULT_MASTER_POLICY, now: now - 60 });
  const pair = await generateKeyPair('RS256', { extractable: true });
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: settings.ACCESS_ISSUER, aud: settings.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});

const read = async (path: string) => {
  const response = await operatorService(new Request(`${settings.OPERATOR_ORIGIN}/admin/v1/${path}`,
    { headers: { 'Cf-Access-Jwt-Assertion': jwt } }), settings, resolver, new Date(now * 1000));
  const body = await response.json() as Record<string, any>;
  if (response.status !== 200) throw new Error(`${path}: ${response.status} ${JSON.stringify(body)}`);
  return body;
};

describe('external seed submissions in operator insights', () => {
  it('reads seeded uploads, labels and the seed principal without failing', async () => {
    const version = await db.prepare(`SELECT v.version_id AS versionId, v.work_id AS workId, f.filter_id AS filterId
      FROM versions v JOIN master_filters f USING (version_id)`).first<{ versionId: string; workId: string; filterId: string }>();
    const seed = 'external-seed-videoskip';
    for (const path of ['home', 'items', `items/${version!.workId}/versions/${version!.versionId}`, `filters/${version!.filterId}/context`,
      'contributors/leaderboard?metric=uploads', 'queue', 'search?q=videoskip', 'audit']) await read(path);
    const item = await read(`items/${version!.workId}`);
    expect(JSON.stringify(item)).toContain('The Matrix');
    expect(JSON.stringify(item)).toContain('VideoSkip');
    const activity = await read('activity?kind=uploads');
    expect(activity.items[0].actor).toMatchObject({ type: 'contributor', contributorId: seed, displayName: 'VideoSkip external archive' });
    const profile = await read(`contributors/${seed}/profile`);
    expect(profile).toMatchObject({ identity: { contributorId: seed, state: 'suspended' } });
  });
});
