import { z } from 'zod';
import type { Env } from '../config';
import { BodyLimitError, readBoundedBytes } from '../http/bounded-body';
import { inspectModeration, moderate, ModerationError, resourceId } from './moderation';
import { operatorReadAdmission, operatorFailure as failure } from './admission';
export { moderationLimits } from './admission';

export async function operatorModeration(request: Request, env: Env, subject: string, now: number): Promise<Response> {
  return operatorReadAdmission(env, now, async (db, requestBytes) => {
  let output: Response;
  try {
    const url = new URL(request.url), path = url.pathname;
    if (request.headers.has('content-encoding')) return failure(400, 'invalid_moderation');
    if (path === '/admin/v1/moderation') {
      if (request.method !== 'POST') output = failure(405, 'method_not_allowed');
      else if (url.search || request.headers.get('content-type') !== 'application/json') output = failure(400, 'invalid_moderation');
      else {
        const body = await readBoundedBytes(request, requestBytes);
        output = Response.json(await moderate(db, subject, JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)), now));
      }
    } else if (path === '/admin/v1/reports') {
      if (request.method !== 'GET') output = failure(405, 'method_not_allowed');
      else {
        const after = url.searchParams.get('after');
        if ([...url.searchParams.keys()].some(key => key !== 'after') || url.searchParams.getAll('after').length > 1)
          throw new Error('invalid_moderation');
        if (after !== null) resourceId.parse(after);
        const rows = await db.prepare(`SELECT report_id AS reportId, contributor_id AS contributorId, revision_id AS revisionId,
          reason, note, created_at AS createdAt FROM reports WHERE state = 'open' AND report_id > ? ORDER BY report_id LIMIT 21`)
          .bind(after ?? '').all();
        output = Response.json({ version: 1, reports: rows.results.slice(0, 20),
          nextCursor: rows.results.length > 20 ? rows.results[19].reportId : null });
      }
    } else {
      const match = path.match(/^\/admin\/v1\/moderation\/(revision|contributor|vote|report)\/([A-Za-z0-9_.-]{1,128})(?:\/([A-Za-z0-9_.-]{1,128}))?$/);
      if (!match) output = failure(404, 'not_found');
      else if (request.method !== 'GET') output = failure(405, 'method_not_allowed');
      else if (url.search) output = failure(400, 'invalid_moderation');
      else output = Response.json(await inspectModeration(db, match[1], match[2], match[3]));
    }
  } catch (error) {
    if (error instanceof BodyLimitError) output = failure(413, 'body_too_large');
    else if (error instanceof z.ZodError || error instanceof SyntaxError || error instanceof TypeError || String(error).includes('invalid_moderation')) output = failure(400, 'invalid_moderation');
    else if (error instanceof ModerationError) output = failure(error.code === 'resource_not_found' ? 404 : error.code === 'moderation_ineligible' ? 403 : 409, error.code);
    else if (String(error).includes('operator_rate_limited')) output = failure(429, 'operator_rate_limited');
    else if (String(error).includes('download_query_limit')) output = failure(413, 'moderation_query_limit');
    else output = failure(503, 'service_unavailable');
  }
  return output;
  });
}