import type { Env } from '../../config';
import type { SqlDatabase } from '../../runtime/contracts';
import { operatorFailure, operatorReadAdmission } from '../admission';
import { activityPage } from './activity';
import { auditPage } from './audit';
import { filterContext } from './filter-context';
import { home } from './home';
import { itemDetail } from './item-detail';
import { itemsPage } from './items';
import { leaderboardPage } from './leaderboard';
import { contributorProfile } from './profile';
import { InsightNotFound, InsightQueryError } from './query';
import { queuePage } from './queue';
import { search } from './search';
import { versionDetail } from './version-detail';

const ID = '([A-Za-z0-9_.-]{1,128})';
const routes: [RegExp, (url: URL, match: RegExpMatchArray, env: Env) => (db: SqlDatabase, now: number) => Promise<unknown>][] = [
  [/^\/admin\/v1\/home$/, (url, _, env) => (db, now) => home(db, env, url, now)],
  [/^\/admin\/v1\/items$/, url => (db, now) => itemsPage(db, url, now)],
  [new RegExp(`^/admin/v1/items/${ID}$`), (url, m) => (db, now) => itemDetail(db, url, m[1], now)],
  [new RegExp(`^/admin/v1/items/${ID}/versions/${ID}$`), (url, m) => (db, now) => versionDetail(db, url, m[1], m[2], now)],
  [new RegExp(`^/admin/v1/filters/${ID}/context$`), (url, m) => (db, now) => filterContext(db, url, m[1], now)],
  [/^\/admin\/v1\/activity$/, url => (db, now) => activityPage(db, url, now)],
  [/^\/admin\/v1\/contributors\/leaderboard$/, url => (db, now) => leaderboardPage(db, url, now)],
  [new RegExp(`^/admin/v1/contributors/${ID}/profile$`), (url, m) => (db, now) => contributorProfile(db, url, m[1], now)],
  [/^\/admin\/v1\/queue$/, url => (db, now) => queuePage(db, url, now)],
  [/^\/admin\/v1\/search$/, url => (db, now) => search(db, url, now)],
  [/^\/admin\/v1\/audit$/, url => (db, now) => auditPage(db, url, now)],
];

/** True for the read-only operator insight routes served by `operatorInsights`. */
export function isInsightPath(path: string) {
  return routes.some(([pattern]) => pattern.test(path));
}

/**
 * Serve an operator insight read (home, items, activity, contributors, queue, search, audit) through
 * the bounded read admission. `now` is Unix seconds; responses carry epoch milliseconds.
 */
export async function operatorInsights(request: Request, env: Env, now: number): Promise<Response> {
  if (request.method !== 'GET') return operatorFailure(405, 'method_not_allowed');
  const url = new URL(request.url);
  const route = routes.map(([pattern, handler]) => [url.pathname.match(pattern), handler] as const).find(([match]) => match);
  if (!route) return operatorFailure(404, 'not_found');
  const read = route[1](url, route[0]!, env);
  return operatorReadAdmission(env, now, async db => {
    try { return Response.json(await read(db, now)); }
    catch (error) {
      if (error instanceof InsightQueryError) return operatorFailure(400, error.code);
      if (error instanceof InsightNotFound) return operatorFailure(404, 'resource_not_found');
      throw error;
    }
  });
}
