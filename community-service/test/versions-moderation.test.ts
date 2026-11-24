import { beforeAll, describe, expect, it } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT, type JWTVerifyGetKey } from 'jose';
import { tunnelActor } from '../src/runtime/environment.mjs';
import { operatorService } from '../src/admin/service';
import { acceptVersionForSubmission } from '../src/versions/acceptance-service';
import { listAliases } from '../src/versions/labels';
import { CatalogModerationError, moderateCatalog } from '../src/versions/moderation';
import { db, keys, newId, now, policy, setupVersionCatalog, submission } from './helpers/versions';

const env = { APP_ENV: 'local', TRUSTED_ACTOR_KEY: tunnelActor, CATALOG_DB: db, OPERATOR_ORIGIN: 'https://operator.example.test',
  ACCESS_ISSUER: 'https://test.cloudflareaccess.com', ACCESS_AUDIENCE: 'operator-app', OPERATOR_SUBJECTS: '["operator-subject"]',
  MODERATION_LIMITS: JSON.stringify({ requestBytes: 8192, maxQueries: 64 }), MASTER_POLICY: JSON.stringify(policy) };
let jwt: string, resolver: JWTVerifyGetKey, signer: CryptoKey;
const accept = (overrides: Parameters<typeof submission>[0]) => acceptVersionForSubmission(db, submission(overrides), { policy, now, newId });
const work = (tmdb: string) => ({ ...submission().work, tmdb, title: `Work ${tmdb}` });
const action = (fields: Record<string, unknown>) => ({ version: 1, actionId: crypto.randomUUID(), expectedRevision: 0,
  reason: 'Reviewed evidence', ...fields });
const moderate = (fields: Record<string, unknown>) => moderateCatalog(db, 'operator-subject', action(fields), now, { policy });
function request(path = '/admin/v1/moderation', body?: unknown) {
  return new Request(`${env.OPERATOR_ORIGIN}${path}`, { method: body ? 'POST' : 'GET', headers: {
    'Cf-Access-Jwt-Assertion': jwt, Origin: env.OPERATOR_ORIGIN, 'X-Community-Operator': '1',
    'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin',
  }, ...(body ? { body: JSON.stringify(body) } : {}) });
}
const handle = (input: Request, settings: Record<string, unknown> = env) =>
  operatorService(input, settings, resolver, new Date(now * 1000));
const failure = async (promise: Promise<unknown>) => promise.then(() => null, (error: CatalogModerationError) => error.code);

beforeAll(async () => {
  await setupVersionCatalog();
  const pair = await generateKeyPair('RS256', { extractable: true });
  signer = pair.privateKey;
  resolver = createLocalJWKSet({ keys: [{ ...await exportJWK(pair.publicKey), kid: 'fixture', alg: 'RS256' }] });
  jwt = await new SignJWT({ sub: 'operator-subject', iss: env.ACCESS_ISSUER, aud: env.ACCESS_AUDIENCE,
    exp: now + 60, iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(pair.privateKey);
});

describe('confirm_version over the admin API', () => {
  it('confirms a candidate once, replays retries and refuses stale or ineligible actions', async () => {
    await accept({ work: work('10'), matchKeys: [keys.md5('1')] });
    const candidate = await accept({ work: work('10'), contributorId: 'c2', matchKeys: [keys.md5('2')] });
    const body = action({ command: 'confirm_version', resource: 'version', targetId: candidate.versionId });
    const applied = await handle(request(undefined, body));
    expect([applied.status, await applied.json()]).toEqual([200, { version: 1, actionId: body.actionId,
      resource: `version:${candidate.versionId}`, revision: 1, outcome: 'applied' }]);
    expect((await handle(request(undefined, body))).status).toBe(200);
    const stale = await handle(request(undefined, { ...body, actionId: crypto.randomUUID() }));
    expect([stale.status, await stale.json()]).toEqual([409, { error: { code: 'moderation_conflict' } }]);
    const ineligible = await handle(request(undefined, { ...body, actionId: crypto.randomUUID(), expectedRevision: 1 }));
    expect([ineligible.status, await ineligible.json()]).toEqual([403, { error: { code: 'moderation_ineligible' } }]);
    const inspection = await (await handle(request(`/admin/v1/moderation/version/${candidate.versionId}`))).json() as
      { revision: number; state: { status: string }; audit: { outcome: string }[] };
    expect(inspection).toMatchObject({ revision: 1, state: { status: 'confirmed' } });
    expect(inspection.audit.map(row => row.outcome).sort()).toEqual(['applied', 'conflict', 'denied']);
    const missing = await handle(request(undefined, action({ command: 'confirm_version', resource: 'version', targetId: 'v_missing' })));
    expect(missing.status).toBe(404);
    for (const invalid of [{ command: 'confirm_version', resource: 'alias_suggestion', targetId: 'x' },
      { command: 'confirm_version', resource: 'version', targetId: 'x', intoVersionId: 'y' },
      { command: 'approve_alias', resource: 'alias_suggestion', targetId: 'x', aliasId: 'extended', newAliasId: 'a', label: 'A' }]) {
      expect((await handle(request(undefined, action(invalid)))).status).toBe(400);
    }
  });
});

describe('merge_version', () => {
  it('moves keys and acceptances into a same-work target and flattens earlier merges', async () => {
    const target = await accept({ work: work('20'), matchKeys: [keys.md5('3'), keys.audio('3')], durationMs: 1000 });
    const source = await accept({ work: work('20'), contributorId: 'c2', matchKeys: [keys.md5('4')], durationMs: 3000 });
    const older = await accept({ work: work('20'), contributorId: 'c3', matchKeys: [keys.md5('5')], durationMs: 5000 });
    await moderate({ command: 'merge_version', resource: 'version', targetId: older.versionId, intoVersionId: source.versionId });
    await db.prepare(`INSERT INTO version_match_keys SELECT ?, type, algorithm, value, size_bytes, 0, 1 FROM version_match_keys
      WHERE version_id = ? AND type = 'audio_stream'`).bind(source.versionId, target.versionId).run();
    expect(await moderate({ command: 'merge_version', resource: 'version', targetId: source.versionId, intoVersionId: target.versionId,
      expectedRevision: 1 })).toMatchObject({ outcome: 'applied', revision: 2 });
    expect(await db.prepare(`SELECT version_id, status, merged_into, runtime_ms FROM versions WHERE version_id IN (?, ?, ?) ORDER BY created_seq`)
      .bind(target.versionId, source.versionId, older.versionId).all().then(r => r.results)).toEqual([
      { version_id: target.versionId, status: 'confirmed', merged_into: null, runtime_ms: 3000 },
      { version_id: source.versionId, status: 'merged_into', merged_into: target.versionId, runtime_ms: 4000 },
      { version_id: older.versionId, status: 'merged_into', merged_into: target.versionId, runtime_ms: 5000 }]);
    expect(await db.prepare('SELECT type, value, is_reference, observations FROM version_match_keys WHERE version_id = ? ORDER BY type, value')
      .bind(target.versionId).all().then(r => r.results.map(row => [row.type, (row.value as string)[0], row.is_reference, row.observations])))
      .toEqual([['audio_stream', '3', 1, 2], ['md5_quick', '3', 1, 1], ['md5_quick', '4', 0, 1], ['md5_quick', '5', 0, 1]]);
    expect(await db.prepare('SELECT count(*) AS n FROM version_match_keys WHERE version_id IN (?, ?)')
      .bind(source.versionId, older.versionId).first('n')).toBe(0);
    expect(await db.prepare(`SELECT contributor_id, tier, founder, merged_from FROM version_acceptances WHERE version_id = ?
      ORDER BY accepted_seq`).bind(target.versionId).all().then(r => r.results)).toEqual([
      { contributor_id: 'c1', tier: 2, founder: 1, merged_from: null },
      { contributor_id: 'c2', tier: null, founder: 0, merged_from: source.versionId },
      { contributor_id: 'c3', tier: null, founder: 0, merged_from: older.versionId }]);
    const attached = await accept({ work: work('20'), contributorId: 'c4', matchKeys: [keys.md5('4')] });
    expect(attached).toMatchObject({ versionId: target.versionId, disposition: 'attached' });
  });

  it('refuses invalid merges', async () => {
    const one = await accept({ work: work('30') }), other = await accept({ work: work('31') });
    const merged = await accept({ work: work('30'), contributorId: 'c2', version: { choice: 'other', text: null } });
    const kept = await accept({ work: work('30'), contributorId: 'c3', version: { choice: 'other', text: null } });
    await moderate({ command: 'merge_version', resource: 'version', targetId: merged.versionId, intoVersionId: kept.versionId });
    const merge = (targetId: string, intoVersionId: string) =>
      failure(moderate({ command: 'merge_version', resource: 'version', targetId, intoVersionId }));
    expect(await merge(one.versionId, other.versionId)).toBe('merge_invalid');
    expect(await merge(one.versionId, one.versionId)).toBe('merge_invalid');
    expect(await merge(one.versionId, merged.versionId)).toBe('merge_invalid');
    expect(await merge(one.versionId, 'v_missing')).toBe('merge_invalid');
    expect(await merge('v_missing', one.versionId)).toBe('resource_not_found');
  });
});

describe('alias suggestions', () => {
  it('approves new and existing aliases, relabels matching versions and refuses taken spellings', async () => {
    const imax = await accept({ work: work('40'), version: { choice: 'other', text: 'IMAX Enhanced' } });
    const again = await accept({ work: work('41'), version: { choice: 'other', text: 'imax enhanced' } });
    const cut = await accept({ work: work('42'), version: { choice: 'other', text: 'Ultimate Cut' } });
    const junk = await accept({ work: work('43'), version: { choice: 'other', text: 'spam' } });
    const pending = await (await handle(request('/admin/v1/alias-suggestions?state=pending'))).json() as
      { suggestions: { suggestionId: string }[]; nextCursor: string | null };
    expect(pending.suggestions.map(item => item.suggestionId)).toEqual(expect.arrayContaining(
      [imax, again, cut, junk].map(item => item.aliasSuggestion!.suggestionId)));
    expect(await moderate({ command: 'approve_alias', resource: 'alias_suggestion', targetId: imax.aliasSuggestion!.suggestionId,
      newAliasId: 'imax', label: 'IMAX Enhanced' })).toMatchObject({ outcome: 'applied' });
    expect(await db.prepare('SELECT alias_id, revision FROM versions WHERE version_id = ?').bind(imax.versionId).first())
      .toEqual({ alias_id: 'imax', revision: 2 });
    expect(await db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(`version:${again.versionId}`)
      .first('revision')).toBe(1);
    expect((await listAliases(db)).map(alias => alias.aliasId)).toEqual(['theatrical', 'directors_cut', 'extended', 'imax', 'unrated']);
    expect(await failure(moderate({ command: 'approve_alias', resource: 'alias_suggestion',
      targetId: again.aliasSuggestion!.suggestionId, aliasId: 'extended' }))).toBe('alias_conflict');
    expect(await failure(moderate({ command: 'approve_alias', resource: 'alias_suggestion',
      targetId: cut.aliasSuggestion!.suggestionId, newAliasId: 'imax', label: 'Again' }))).toBe('alias_conflict');
    expect(await failure(moderate({ command: 'approve_alias', resource: 'alias_suggestion',
      targetId: cut.aliasSuggestion!.suggestionId, aliasId: 'missing' }))).toBe('invalid_moderation');
    await moderate({ command: 'approve_alias', resource: 'alias_suggestion', targetId: cut.aliasSuggestion!.suggestionId, aliasId: 'extended' });
    expect(await accept({ work: work('42'), contributorId: 'c2', version: { choice: 'alias', aliasId: 'extended' } }))
      .toMatchObject({ disposition: 'created_candidate' });
    const rejected = await handle(request(undefined, action({ command: 'reject_alias', resource: 'alias_suggestion',
      targetId: junk.aliasSuggestion!.suggestionId })));
    expect(rejected.status).toBe(200);
    expect(await failure(moderate({ command: 'reject_alias', resource: 'alias_suggestion', targetId: junk.aliasSuggestion!.suggestionId,
      expectedRevision: 1 }))).toBe('moderation_ineligible');
    const conflict = await handle(request(undefined, action({ command: 'approve_alias', resource: 'alias_suggestion',
      targetId: again.aliasSuggestion!.suggestionId, aliasId: 'extended' })));
    expect([conflict.status, await conflict.json()]).toEqual([409, { error: { code: 'alias_conflict' } }]);
  });
});

describe('admin version lists', () => {
  it('pages candidates 20 at a time and validates queries', async () => {
    for (let index = 0; index < 21; index++) await accept({ work: work(String(500 + index)), version: { choice: 'other', text: null } });
    const first = await (await handle(request('/admin/v1/versions?status=candidate'))).json() as
      { versions: { versionId: string; status: string }[]; nextCursor: string };
    expect(first.versions).toHaveLength(20);
    expect(first.versions.every(item => item.status === 'candidate')).toBe(true);
    const second = await (await handle(request(`/admin/v1/versions?status=candidate&after=${first.nextCursor}`))).json() as
      { versions: { versionId: string }[] };
    expect(second.versions[0].versionId > first.nextCursor).toBe(true);
    for (const path of ['/admin/v1/versions?status=bogus', '/admin/v1/versions?other=1', '/admin/v1/alias-suggestions?state=a&state=b'])
      expect((await handle(request(path))).status).toBe(400);
  });
});

describe('admin safety', () => {
  it('serializes concurrent A→B and B→C merges without merging into a merged version', async () => {
    const a = await accept({ work: work('60') });
    const b = await accept({ work: work('60'), contributorId: 'c2', version: { choice: 'other', text: null } });
    const c = await accept({ work: work('60'), contributorId: 'c3', version: { choice: 'other', text: null } });
    const outcomes = await Promise.allSettled([
      moderate({ command: 'merge_version', resource: 'version', targetId: a.versionId, intoVersionId: b.versionId }),
      moderate({ command: 'merge_version', resource: 'version', targetId: b.versionId, intoVersionId: c.versionId })]);
    expect(outcomes.filter(item => item.status === 'fulfilled')).toHaveLength(1);
    const rejected = outcomes.find(item => item.status === 'rejected') as PromiseRejectedResult;
    expect(['moderation_conflict', 'merge_invalid']).toContain((rejected.reason as CatalogModerationError).code);
    expect(await db.prepare(`SELECT count(*) AS n FROM versions v JOIN versions t ON t.version_id = v.merged_into
      WHERE t.status = 'merged_into'`).first('n')).toBe(0);
    await expect(db.prepare(`UPDATE versions SET status = 'merged_into', merged_into = ? WHERE version_id = ?`)
      .bind(a.versionId, c.versionId).run()).rejects.toThrow(/merge_invalid|immutable_version/);
  });

  it('fails closed with 503 when MASTER_POLICY is missing or invalid', async () => {
    const candidate = await accept({ work: work('61'), version: { choice: 'other', text: null } });
    const body = () => action({ command: 'confirm_version', resource: 'version', targetId: candidate.versionId });
    for (const settings of [{ ...env, MASTER_POLICY: undefined }, { ...env, MASTER_POLICY: '{"matchToleranceMs":500}' }]) {
      const response = await handle(request(undefined, body()), settings);
      expect([response.status, await response.json()]).toEqual([503, { error: { code: 'moderation_not_configured' } }]);
    }
    expect(await db.prepare('SELECT status FROM versions WHERE version_id = ?').bind(candidate.versionId).first('status')).toBe('candidate');
  });

  it('requires operator authorization on list and inspection routes', async () => {
    const other = await new SignJWT({ sub: 'someone-else', iss: env.ACCESS_ISSUER, aud: env.ACCESS_AUDIENCE, exp: now + 60,
      iat: now - 60, type: 'app' }).setProtectedHeader({ alg: 'RS256', kid: 'fixture' }).sign(signer);
    for (const path of ['/admin/v1/versions', '/admin/v1/alias-suggestions', '/admin/v1/moderation/version/v_x',
      '/admin/v1/moderation/alias_suggestion/as_x']) {
      const anonymous = request(path);
      anonymous.headers.delete('Cf-Access-Jwt-Assertion');
      expect((await handle(anonymous)).status).toBe(401);
      const forbidden = request(path);
      forbidden.headers.set('Cf-Access-Jwt-Assertion', other);
      expect((await handle(forbidden)).status).toBe(403);
    }
  });
});
