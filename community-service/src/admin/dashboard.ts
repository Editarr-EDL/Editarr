import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import type { Env } from '../config';
import { serviceUsage } from '../integrations/discord/usage';
import { resourceId } from './moderation';
import { operatorReadAdmission, operatorFailure } from './admission';

const pageSize = 20;
const cursorSchema = z.array(z.string().min(1).max(128)).min(1).max(3);
type Row = Record<string, string | number | null>;
const unavailable = { source: 'unavailable', observedAt: null, value: null, providerVerified: false };

function cursor(url: URL, width: number): string[] {
  if ([...url.searchParams.keys()].some(key => key !== 'after') || url.searchParams.getAll('after').length > 1)
    throw new Error('invalid_dashboard_query');
  const value = url.searchParams.get('after');
  if (!value) {
    if (value !== null) throw new Error('invalid_dashboard_query');
    return Array.from({ length: width }, () => '');
  }
  if (value.length > 1024 || !/^[A-Za-z0-9+/=]+$/.test(value)) throw new Error('invalid_dashboard_query');
  const decoded = cursorSchema.parse(JSON.parse(atob(value)));
  if (decoded.length !== width) throw new Error('invalid_dashboard_query');
  return decoded;
}

async function page(db: SqlDatabase, sql: string, values: string[], keys: string[], now: number) {
  const rows = (await db.prepare(sql).bind(...values).all<Row>()).results;
  const items = rows.slice(0, pageSize);
  return { version: 1, observedAt: now * 1000, source: 'service_database', items,
    nextCursor: rows.length > pageSize ? btoa(JSON.stringify(keys.map(key => String(items[pageSize - 1][key])))) : null };
}

async function overview(db: SqlDatabase, now: number) {
  const day = new Date(now * 1000).toISOString().slice(0, 10);
  const storage = await db.prepare("SELECT bytes FROM artifact_storage_usage WHERE scope_id = 'global'").first<{ bytes: number }>();
  const activity = await serviceUsage(db, now * 1000);
  const globalBytes = Number(storage?.bytes ?? 0);
  return { version: 1, observedAt: now * 1000, period: day,
    storage: { source: 'artifact_storage_usage', observedAt: now * 1000, providerVerified: false,
      globalBytes },
    databaseSize: unavailable, providerUsage: unavailable, backup: unavailable,
    activity, observations: [] };
}

export async function operatorDashboard(request: Request, env: Env, now: number) {
  if (request.method !== 'GET') return operatorFailure(405, 'method_not_allowed');
  return operatorReadAdmission(env, now, async db => {
    try {
      const url = new URL(request.url), path = url.pathname;
      if (path === '/admin/v1/dashboard/overview') {
        if (url.search) throw new Error('invalid_dashboard_query');
        return Response.json(await overview(db, now));
      }
      if (path === '/admin/v1/dashboard/contributors') return Response.json(await page(db,
        `SELECT contributor_id AS contributorId, display_name AS displayName, state, created_at AS createdAt
          FROM contributors WHERE contributor_id > ? ORDER BY contributor_id LIMIT 21`, cursor(url, 1), ['contributorId'], now));
      const contributor = path.match(/^\/admin\/v1\/dashboard\/contributors\/([A-Za-z0-9_.-]{1,128})\/(keys|usage)$/);
      if (contributor) {
        const id = resourceId.parse(contributor[1]);
        if (!await db.prepare('SELECT contributor_id FROM contributors WHERE contributor_id = ?').bind(id).first())
          return operatorFailure(404, 'resource_not_found');
        if (contributor[2] === 'keys') return Response.json(await page(db,
          `SELECT k.key_id AS keyId, k.installation_id AS installationId, k.state,
            k.created_at AS createdAt, k.retired_at AS retiredAt FROM installations i
            JOIN installation_keys k USING (installation_id) WHERE i.contributor_id = ? AND k.key_id > ?
            ORDER BY k.key_id LIMIT 21`, [id, ...cursor(url, 1)], ['keyId'], now));
        if (url.search) throw new Error('invalid_dashboard_query');
        const day = new Date(now * 1000).toISOString().slice(0, 10);
        const storage = await db.prepare('SELECT bytes FROM artifact_storage_usage WHERE scope_id = ?')
          .bind(`contributor:${id}`).first<{ bytes: number }>();
        const observations = (await db.prepare(`SELECT resource, outcome, occurrences, units, updated_at AS observedAt
          FROM usage_observations WHERE contributor_id = ? AND period = ?
          AND resource IN ('worker_requests', 'd1_writes') ORDER BY resource, outcome`).bind(id, day).all()).results;
        return Response.json({ version: 1, contributorId: id, observedAt: now * 1000, period: day,
          storage: { source: 'artifact_storage_usage', observedAt: now * 1000, bytes: storage?.bytes ?? 0 },
          observations });
      }
      const version = path.match(/^\/admin\/v1\/dashboard\/versions\/([A-Za-z0-9_.-]{1,128})\/(filters|submissions)$/);
      if (version) {
        const id = resourceId.parse(version[1]);
        if (!await db.prepare('SELECT version_id FROM versions WHERE version_id = ?').bind(id).first())
          return operatorFailure(404, 'resource_not_found');
        if (version[2] === 'filters') return Response.json(await page(db,
          `SELECT r.filter_id AS filterId, r.state, r.quarantined, f.category, f.subcategory, f.severity, f.action,
            f.start_ms AS startMs, f.end_ms AS endMs, f.confidence, f.contributor_count AS contributorCount,
            f.attestations, f.upvotes, f.downvotes, f.disputes, f.served FROM filter_registry r
            LEFT JOIN master_filters f USING (filter_id) WHERE r.version_id = ? AND r.filter_id > ?
            ORDER BY r.filter_id LIMIT 21`, [id, ...cursor(url, 1)], ['filterId'], now));
        return Response.json(await page(db,
          `SELECT s.submission_id AS submissionId, s.contributor_id AS contributorId, s.state, s.tier,
            a.quarantined, s.created_at AS createdAt FROM submissions s JOIN version_acceptances a USING (submission_id)
            WHERE s.version_id = ? AND s.submission_id > ? ORDER BY s.submission_id LIMIT 21`,
          [id, ...cursor(url, 1)], ['submissionId'], now));
      }
      if (path === '/admin/v1/dashboard/operations') return Response.json(await page(db,
        `SELECT contributor_id AS contributorId, kind, operation_id AS operationId, state,
          created_at AS createdAt, updated_at AS updatedAt FROM operations
          WHERE (contributor_id, kind, operation_id) > (?, ?, ?) ORDER BY contributor_id, kind, operation_id LIMIT 21`,
        cursor(url, 3), ['contributorId', 'kind', 'operationId'], now));
      if (path === '/admin/v1/dashboard/audit') return Response.json(await page(db,
        `SELECT * FROM (SELECT action_id AS actionId, actor, resource, command, reason, outcome,
          expected_revision AS expectedRevision, created_at AS createdAt FROM moderation_actions
          UNION ALL SELECT action_id, actor, resource, command, reason, outcome, expected_revision, created_at
          FROM catalog_moderation_actions) WHERE actionId > ? ORDER BY actionId LIMIT 21`, cursor(url, 1), ['actionId'], now));
      return operatorFailure(404, 'not_found');
    } catch (error) {
      if (error instanceof z.ZodError || error instanceof SyntaxError || String(error).includes('invalid_dashboard_query') || error instanceof DOMException)
        return operatorFailure(400, 'invalid_dashboard_query');
      throw error;
    }
  });
}