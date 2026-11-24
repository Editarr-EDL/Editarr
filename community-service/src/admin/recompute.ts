import type { SqlDatabase } from '../runtime/contracts';
import { z } from 'zod';
import { recomputeVersion } from '../master/recompute';
import { privateNote } from './moderation';
import type { MasterModerationContext } from './master-moderation';

const token = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
/** `POST /admin/v1/recompute` (§7.8). */
export const recomputeInput = z.strictObject({ version: z.literal(1), actionId: z.string().uuid(),
  versionIds: z.union([z.literal('all'), z.array(token).min(1).max(100).refine(ids => new Set(ids).size === ids.length)]),
  reason: privateNote.refine(value => value.trim().length > 0) });
/** Versions recomputed per page of an `all` job. */
export const RECOMPUTE_BATCH = 20;

export class RecomputeError extends Error {
  constructor(public code: 'action_conflict' | 'resource_not_found') { super(code); }
}

/**
 * Recompute versions deterministically under the current `MASTER_POLICY` and record one audit
 * row. A list returns each version's resulting revision; `all` pages through every non-merged
 * version in bounded batches and returns the action ID as its `jobId`. A retry of the same action
 * recomputes again, which changes nothing when no input changed.
 */
export async function recomputeAction(db: SqlDatabase, actor: string, value: unknown, context: MasterModerationContext) {
  const input = recomputeInput.parse(value), body = JSON.stringify(input);
  const saved = await db.prepare('SELECT actor, request_json FROM moderation_actions WHERE action_id = ?').bind(input.actionId)
    .first<{ actor: string; request_json: string }>();
  if (saved && (saved.actor !== actor || saved.request_json !== body)) throw new RecomputeError('action_conflict');
  if (input.versionIds !== 'all') {
    const known = await db.prepare(`SELECT version_id FROM versions WHERE status != 'merged_into'
      AND version_id IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(input.versionIds)).all<{ version_id: string }>();
    if (known.results.length !== input.versionIds.length) throw new RecomputeError('resource_not_found');
  }
  if (!saved) await db.prepare(`INSERT INTO moderation_actions (action_id, actor, resource, target_id, command, expected_revision,
    reason, request_json, outcome, error_code, created_at) VALUES (?, ?, 'recompute', ?, 'recompute', 0, ?, ?, 'applied', NULL, ?)
    ON CONFLICT(action_id) DO NOTHING`).bind(input.actionId, actor, input.versionIds === 'all' ? 'all' : 'versions',
    input.reason, body, context.now * 1000).run();
  if (input.versionIds !== 'all') {
    const versions = [];
    for (const versionId of input.versionIds) {
      const plan = await recomputeVersion(db, versionId, context);
      versions.push({ versionId, revision: plan.revision, changed: plan.changed });
    }
    return { version: 1, actionId: input.actionId, versions };
  }
  for (let after = ''; ;) {
    const page = await db.prepare(`SELECT version_id FROM versions WHERE status != 'merged_into' AND version_id > ?
      ORDER BY version_id LIMIT ${RECOMPUTE_BATCH}`).bind(after).all<{ version_id: string }>();
    for (const row of page.results) await recomputeVersion(db, row.version_id, context);
    if (page.results.length < RECOMPUTE_BATCH) break;
    after = page.results.at(-1)!.version_id;
  }
  return { version: 1, jobId: input.actionId };
}
