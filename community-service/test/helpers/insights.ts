import { contributorVotes } from '../../src/http/votes';
import { contributor, support, type Contributor } from './submissions';
import { download, eligible, post, reply, submitWork } from './master';
import { db, now, policy, setupVersionCatalog } from './versions';

const voteLimits = { requestBytes: 8192, maxQueries: 64, votesPerHour: 1000 };

/** Cast a receipt-bound vote through the contributor handler, so `vote_events` records it. */
export async function castVote(who: Contributor, filterId: string, vote: 'up' | 'down' | 'none', receiptId: string) {
  const response = await contributorVotes(await post(who, `/v1/filters/${filterId}/votes`, { version: 1, vote, receiptId }, `vote-${crypto.randomUUID()}`), db,
    'community.example', now, eligible, { policy, limits: voteLimits });
  const result = await reply(response);
  if (result.status !== 200) throw new Error(JSON.stringify(result.body));
}

async function rename(who: Contributor, name: string) {
  await db.prepare('UPDATE contributors SET display_name = ?, display_name_key = ? WHERE contributor_id = ?')
    .bind(name, name.toLowerCase(), who.contributorId).run();
}

const work = (tmdb: string, title: string, year: number) =>
  ({ kind: 'movie' as const, tmdb, tvdb: null, season: null, episode: null, lastEpisode: null, title, year });

export const audit = (id: string, resource: string, command: string, at: number, outcome = 'applied', table = 'moderation_actions') =>
  db.prepare(`INSERT INTO ${table} (action_id, actor, resource, target_id, command, expected_revision, reason, request_json, outcome,
    error_code, created_at) VALUES (?, 'operator-subject', ?, ?, ?, 0, 'Seeded reason', '{}', ?, ?, ?)`)
    .bind(id, resource, resource.split(':')[1] ?? 'all', command, outcome, outcome === 'applied' ? null : 'resource_not_found', at).run();

/**
 * Seed a realistic operator catalog: two directors-cut uploads of Arrival Road (1977), a candidate
 * "Blue Comet: Remastered" upload with a pending alias suggestion, receipt-bound votes, open reports,
 * a quarantined filter, audit rows on missing resources, an untitled work and 22 paging works.
 */
export async function seedInsights() {
  await setupVersionCatalog([]);
  const [mira, jonas, priya, ana] = [await contributor(), await contributor(), await contributor(), await contributor()];
  await rename(mira, 'Mira Okafor');
  await rename(jonas, 'Jonas Lind');
  await rename(priya, 'Priya Raman');
  await rename(ana, 'Ana Ruiz');
  const entries = [support('e1', 600_000, 605_000), support('e2', 1_200_000, 1_210_000, { subcategory: 'stabbing', severity: 'high' })];
  const arrival = await submitWork(mira, '700', { work: work('700', 'Arrival Road', 1977), version: { choice: 'alias', aliasId: 'directors_cut' }, entries });
  await submitWork(jonas, '700', { work: work('700', 'Arrival Road', 1977), version: { choice: 'alias', aliasId: 'directors_cut' }, entries });
  const comet = await submitWork(priya, '800', { work: work('800', 'Blue Comet', 1984),
    version: { choice: 'other', text: 'Blue Comet: Remastered' } });
  const versionId = arrival.versionId as string, workId = arrival.workId as string;
  const priyaDownload = await download(priya, versionId), anaDownload = await download(ana, versionId);
  const [gentle, gore] = [...priyaDownload.snapshot.filters].sort((a, b) => a.startMs - b.startMs).map(filter => filter.filterId as string);
  await castVote(priya, gentle, 'up', priyaDownload.receiptId);
  await castVote(ana, gore, 'down', anaDownload.receiptId);
  const report = (id: string, who: Contributor, type: string, target: string, reason: string, note: string, at: number) =>
    db.prepare(`INSERT INTO reports (report_id, contributor_id, target_type, target_id, key_id, reason, note, state, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)`).bind(id, who.contributorId, type, target, who.keyId, reason, note, at).run();
  await report('r_gore', ana, 'filter', gore, 'inaccurate', 'Ends about four seconds early', now - 3600);
  await report('r_comet', mira, 'version', comet.versionId, 'other', 'This is the Theatrical cut', now - 7200);
  await report('r_missing', ana, 'filter', 'f_deleted', 'spam', 'Target was removed', now - 10_800);
  await db.prepare('UPDATE filter_registry SET quarantined = 1 WHERE filter_id = ?').bind(gentle).run();
  await audit('a1-quarantine', `filter:${gentle}`, 'quarantine', (now - 60) * 1000);
  await audit('a2-missing-filter', 'filter:f_deleted', 'quarantine', (now - 120) * 1000, 'denied');
  await audit('a3-missing-contributor', 'contributor:c_gone', 'suspend', (now - 180) * 1000, 'denied');
  await audit('a4-vote', `vote:${gore}:${ana.contributorId}`, 'exclude_vote', (now - 240) * 1000, 'denied');
  await audit('a5-catalog', `version:${comet.versionId}`, 'confirm_version', (now - 300) * 1000, 'denied', 'catalog_moderation_actions');
  await db.prepare(`INSERT INTO works (work_id, kind, catalog_seq, created_at) VALUES ('w_untitled', 'movie', 0, ?)`).bind(now - 50).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO works (work_id, kind, catalog_seq, created_at) SELECT printf('w_page_%02d', i), 'movie', 0, ? - 100000 - i FROM n`).bind(now).run();
  await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 22)
    INSERT INTO work_titles (work_id, normalized, year, title, observations)
    SELECT printf('w_page_%02d', i), printf('paged film %02d', i), 2000 + i, printf('Paged Film %02d', i), 1 FROM n`).run();
  return { mira, jonas, priya, ana, workId, versionId, cometVersionId: comet.versionId as string, cometWorkId: comet.workId as string,
    suggestionId: comet.aliasSuggestion?.suggestionId as string | undefined, gentle, gore };
}
