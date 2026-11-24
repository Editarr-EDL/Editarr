import { beforeAll, describe, expect, it } from 'vitest';
import { decideVersion, deriveVersionState, integerMedian, versionTrust, wilson, type ExistingVersion } from '../src/versions/acceptance';
import { acceptVersionForSubmission, planVersionAcceptance, VersionAcceptanceError } from '../src/versions/acceptance-service';
import { storedKey } from '../src/versions/keys';
import { normalize } from '../src/versions/normalize';
import { acceptanceQuarantineStatements, refreshVersionStatements } from '../src/versions/repository';
import { catalogPolicy } from '../src/config';
import { db, keys, newId, now, policy, setupVersionCatalog, submission } from './helpers/versions';

beforeAll(() => setupVersionCatalog(['c1', 'c2', 'c3', 'c4', 'c5']));
const accept = (overrides: Parameters<typeof submission>[0]) =>
  acceptVersionForSubmission(db, submission(overrides), { policy, now, newId });
const theatrical = { aliasId: 'theatrical', labelText: null };
const existing = (versionId: string, overrides: Partial<ExistingVersion> = {}): ExistingVersion =>
  ({ versionId, status: 'confirmed', trust: 0.2, label: theatrical, keys: [], ...overrides });

describe('D13 decision (pure)', () => {
  const md5 = storedKey(keys.md5()), full = storedKey(keys.full());
  it('creates a confirmed version for a new alias label and a candidate otherwise', () => {
    expect(decideVersion({ choice: 'alias', label: theatrical, keys: [md5] }, [])).toEqual({ disposition: 'created_confirmed' });
    expect(decideVersion({ choice: 'alias', label: theatrical, keys: [md5] },
      [existing('v_1', { label: { aliasId: 'extended', labelText: null } })])).toEqual({ disposition: 'created_confirmed' });
    expect(decideVersion({ choice: 'alias', label: theatrical, keys: [md5] }, [existing('v_1', { keys: [full] })]))
      .toEqual({ disposition: 'created_candidate' });
    expect(decideVersion({ choice: 'other', label: { aliasId: null, labelText: 'imax' }, keys: [] }, []))
      .toEqual({ disposition: 'created_candidate' });
    expect(decideVersion({ choice: 'alias', label: theatrical, keys: [md5] },
      [existing('v_1', { status: 'merged_into', keys: [md5] })])).toEqual({ disposition: 'created_confirmed' });
  });

  it('attaches to the best equal-label tier 1–3 match', () => {
    const versions = [existing('v_4', { keys: [md5] }), existing('v_3', { status: 'candidate', keys: [full] }),
      existing('v_2', { keys: [full], trust: 0.1 }), existing('v_1', { keys: [full], trust: 0.1 }),
      existing('v_0', { keys: [full], label: { aliasId: 'extended', labelText: null } })];
    expect(decideVersion({ choice: 'other', label: { aliasId: null, labelText: null }, keys: [md5, full] }, versions))
      .toEqual({ disposition: 'attached', versionId: 'v_1', tier: 1 });
    expect(decideVersion({ choice: 'alias', label: theatrical, keys: [md5] }, versions))
      .toEqual({ disposition: 'attached', versionId: 'v_4', tier: 2 });
  });

  it('derives medians, Wilson trust and promotion from counted rows', () => {
    expect([integerMedian([3, 1, 2]), integerMedian([1, 2, 3, 6]), integerMedian([])]).toEqual([2, 2, null]);
    expect([wilson(1, 0), wilson(1, 2), wilson(3, 0), wilson(5, 1.25), wilson(1, 9)])
      .toEqual([0.207654955, 0.117618231, 0.375528264, 0.393857102, 0.046964148]);
    expect(versionTrust(['a', 'b'], ['b', 'c'])).toBe(wilson(2, 1));
    const row = (contributorId: string, acceptedSeq: number, tier: 1 | null, quarantined = false) =>
      ({ contributorId, acceptedSeq, tier, durationMs: acceptedSeq * 1000, quarantined });
    expect(deriveVersionState([row('a', 1, 1), row('a', 2, 1), row('b', 3, null), row('c', 4, 1), row('d', 5, 1, true)],
      'candidate', 3)).toEqual({ runtimeMs: 3000, evidenceContributors: 2, trust: wilson(3, 0), promote: false });
    expect(deriveVersionState([row('a', 1, 1), row('b', 2, 1), row('c', 3, 1)], 'candidate', 3).promote).toBe(true);
  });
});

describe('version acceptance service', () => {
  it('accepts the first alias submission at face value as a confirmed version with reference keys', async () => {
    const first = await accept({ matchKeys: [keys.md5('1'), keys.audio('1')] });
    expect(first).toMatchObject({ disposition: 'created_confirmed', versionStatus: 'confirmed', tier: null, aliasSuggestion: null });
    expect(await db.prepare('SELECT type, is_reference, observations FROM version_match_keys WHERE version_id = ? ORDER BY type')
      .bind(first.versionId).all().then(r => r.results)).toEqual([
      { type: 'audio_stream', is_reference: 1, observations: 1 }, { type: 'md5_quick', is_reference: 1, observations: 1 }]);
    expect(await db.prepare('SELECT status, revision, runtime_ms, reference_submission_id FROM versions WHERE version_id = ?')
      .bind(first.versionId).first()).toMatchObject({ status: 'confirmed', revision: 1, runtime_ms: 8_160_000 });
    expect(await db.prepare('SELECT normalized, year, observations FROM work_titles WHERE work_id = ?').bind(first.workId).first())
      .toEqual({ normalized: 'the matrix', year: 1999, observations: 1 });

    const attached = await accept({ contributorId: 'c2', durationMs: 8_161_000, matchKeys: [keys.md5('1'), keys.full('1')] });
    expect(attached).toMatchObject({ workId: first.workId, versionId: first.versionId, disposition: 'attached', tier: '2' });
    expect(await db.prepare(`SELECT type, is_reference, observations FROM version_match_keys WHERE version_id = ?
      AND type IN ('md5_quick', 'sha256_full') ORDER BY type`).bind(first.versionId).all().then(r => r.results)).toEqual([
      { type: 'md5_quick', is_reference: 1, observations: 2 }, { type: 'sha256_full', is_reference: 0, observations: 1 }]);
    expect(await db.prepare('SELECT runtime_ms, trust FROM versions WHERE version_id = ?').bind(first.versionId).first())
      .toEqual({ runtime_ms: 8_160_500, trust: wilson(2, 0) });

    const fuzzy = await accept({ contributorId: 'c3', matchKeys: [keys.md5('9')] });
    expect(fuzzy).toMatchObject({ workId: first.workId, disposition: 'created_candidate', versionStatus: 'candidate' });
    expect(fuzzy.versionId).not.toBe(first.versionId);
  });

  it('creates candidates for Other with and without text and records pending suggestions', async () => {
    const work = { kind: 'movie' as const, tmdb: '100', tvdb: null, season: null, episode: null, lastEpisode: null, title: 'Other', year: null };
    const blank = await accept({ work, version: { choice: 'other', text: '   ' } });
    expect(blank).toMatchObject({ disposition: 'created_candidate', label: { aliasId: null, labelText: null }, aliasSuggestion: null });
    const text = await accept({ work, version: { choice: 'other', text: 'IMAX Enhanced!' } });
    expect(text).toMatchObject({ disposition: 'created_candidate', label: { aliasId: null, labelText: 'imax enhanced' },
      aliasSuggestion: { text: 'IMAX Enhanced!', state: 'pending' } });
    const known = await accept({ work, version: { choice: 'other', text: 'Director’s Cut' } });
    expect(known).toMatchObject({ disposition: 'created_candidate', label: { aliasId: 'directors_cut', labelText: 'directors cut' },
      aliasSuggestion: null });
    expect(await db.prepare("SELECT normalized, state FROM alias_suggestions WHERE submission_id LIKE 'sub_%'").all()
      .then(r => r.results)).toEqual([{ normalized: 'imax enhanced', state: 'pending' }]);
  });

  it('rejects unknown aliases and missing work identity without writing', async () => {
    const before = await db.prepare('SELECT count(*) AS n FROM master_works').first();
    await expect(accept({ version: { choice: 'alias', aliasId: 'nope' } })).rejects.toEqual(new VersionAcceptanceError('unknown_alias'));
    await expect(accept({ work: { ...submission().work, tmdb: null } })).rejects.toEqual(new VersionAcceptanceError('work_identity_required'));
    expect(await db.prepare('SELECT count(*) AS n FROM master_works').first()).toEqual(before);
  });

  it('promotes a candidate once three contributors attach tier 1–3 evidence, counting the founder', async () => {
    const work = { ...submission().work, tmdb: '200', title: 'Promotion' };
    const founder = await accept({ work, version: { choice: 'other', text: 'Fan Edit' }, matchKeys: [keys.sampled('2')] });
    expect(founder.versionStatus).toBe('candidate');
    const second = await accept({ work, contributorId: 'c2', version: { choice: 'other', text: 'fan edit' }, matchKeys: [keys.sampled('2')] });
    const repeat = await accept({ work, contributorId: 'c2', version: { choice: 'other', text: 'fan edit' }, matchKeys: [keys.video('2')] });
    expect(repeat).toMatchObject({ disposition: 'created_candidate' });
    const again = await accept({ work, contributorId: 'c2', version: { choice: 'other', text: 'FAN EDIT' }, matchKeys: [keys.sampled('2')] });
    expect([second, again].map(item => [item.versionId, item.versionStatus, item.promoted]))
      .toEqual([[founder.versionId, 'candidate', false], [founder.versionId, 'candidate', false]]);
    const third = await accept({ work, contributorId: 'c3', version: { choice: 'other', text: 'Fan-Edit' }, matchKeys: [keys.sampled('2')] });
    expect(third).toMatchObject({ versionId: founder.versionId, versionStatus: 'confirmed', promoted: true, tier: '2' });
    expect(await db.prepare('SELECT status FROM versions WHERE version_id = ?').bind(founder.versionId).first('status')).toBe('confirmed');
  });

  it('does not count a keyless founder and recomputes after quarantine', async () => {
    const work = { ...submission().work, tmdb: '300', title: 'Keyless' };
    const single = { candidatePromotionContributors: 1 };
    const founder = await acceptVersionForSubmission(db, submission({ work, version: { choice: 'other', text: null } }),
      { policy: single, now, newId });
    const keyed = await acceptVersionForSubmission(db, submission({ work, contributorId: 'c2', durationMs: 1000,
      version: { choice: 'other', text: null }, matchKeys: [keys.full('3')] }), { policy: single, now, newId });
    expect([founder, keyed].map(item => [item.disposition, item.versionStatus, item.promoted]))
      .toEqual([['created_candidate', 'candidate', false], ['created_candidate', 'confirmed', true]]);
    const row = await db.prepare('SELECT submission_id FROM version_acceptances WHERE version_id = ?').bind(keyed.versionId)
      .first<{ submission_id: string }>();
    await db.batch(await acceptanceQuarantineStatements(db, row!.submission_id, true, 1));
    const read = () => db.prepare('SELECT status, runtime_ms, trust FROM versions WHERE version_id = ?').bind(keyed.versionId).first();
    expect(await read()).toEqual({ status: 'confirmed', runtime_ms: null, trust: wilson(0, 0) });
    await db.batch(await acceptanceQuarantineStatements(db, row!.submission_id, false, 1));
    await db.batch(await refreshVersionStatements(db, keyed.versionId, 1));
    expect(await read()).toEqual({ status: 'confirmed', runtime_ms: 1000, trust: wilson(1, 0) });
  });

  it('resolves works by either identifier and records TMDB/TVDB conflicts for moderation', async () => {
    const episode = { kind: 'episode' as const, tmdb: null, tvdb: '81189', season: 1, episode: 2, lastEpisode: null, title: 'Pilot', year: 2008 };
    const tvdb = await accept({ work: episode });
    const both = await accept({ contributorId: 'c2', work: { ...episode, tmdb: '1396' } });
    expect(both.workId).toBe(tvdb.workId);
    const other = await accept({ work: { ...episode, tvdb: null, tmdb: '9999' } });
    const conflict = await accept({ contributorId: 'c3', work: { ...episode, tmdb: '9999', tvdb: '81189' } });
    expect(conflict).toMatchObject({ workId: other.workId, identifierConflicts: 1 });
    expect(await db.prepare('SELECT provider, value, existing_work_id FROM work_identifier_conflicts').all().then(r => r.results))
      .toEqual([{ provider: 'tvdb', value: '81189', existing_work_id: tvdb.workId }]);
    const multi = await accept({ work: { ...episode, lastEpisode: 3 } });
    expect(multi.workId).not.toBe(tvdb.workId);
  });

  it('plans statements for the submission batch without writing', async () => {
    const plan = await planVersionAcceptance(db, submission({ work: { ...submission().work, tmdb: '400' } }), { policy, now, newId });
    expect(plan.statements.length).toBeGreaterThan(3);
    expect(await db.prepare("SELECT count(*) AS n FROM master_work_identifiers WHERE value = '400'").first('n')).toBe(0);
  });
});

describe('seeds and policy', () => {
  it('seeds §3.4 spellings normalized and unique', async () => {
    const seeds: Record<string, string[]> = {
      theatrical: ['', 'Theatrical', 'Theatrical Cut', 'Theatrical Version', 'Theatrical Edition', 'Theatrical Release',
        'Original Theatrical', 'Original Theatrical Cut'],
      directors_cut: ["Director's Cut", 'Directors Cut', "Director's Edition", "Director's Version"],
      extended: ['Extended Edition', 'Extended', 'Extended Cut', 'Extended Version'],
      unrated: ['Unrated', 'Unrated Cut', 'Unrated Edition', 'Unrated Version'],
    };
    const expected = Object.entries(seeds).flatMap(([alias, spellings]) => [...new Set(spellings.map(normalize))]
      .map(normalized => ({ alias_id: alias, normalized }))).sort((a, b) => a.normalized < b.normalized ? -1 : 1);
    expect(await db.prepare("SELECT alias_id, normalized FROM alias_spellings WHERE source = 'seed' ORDER BY normalized").all()
      .then(r => r.results)).toEqual(expected);
  });

  it('reads candidatePromotionContributors from MASTER_POLICY with a default', () => {
    expect(catalogPolicy({})).toEqual({ candidatePromotionContributors: 3 });
    expect(catalogPolicy({ MASTER_POLICY: JSON.stringify({ candidatePromotionContributors: 5, matchToleranceMs: 500 }) }))
      .toEqual({ candidatePromotionContributors: 5 });
    expect(() => catalogPolicy({ MASTER_POLICY: '{"matchToleranceMs":500}' })).toThrow();
  });
});
