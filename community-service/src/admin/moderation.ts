import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { z } from 'zod';

export const resourceId = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
export const privateNote = z.string().max(1000).refine(value => !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value));
export const moderationInput = z.strictObject({ version: z.literal(1), actionId: z.string().uuid(),
  command: z.enum(['quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report']),
  targetId: resourceId, revisionId: resourceId.optional(), expectedRevision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
  reason: privateNote.refine(value => value.trim().length > 0),
}).refine(value => value.command.endsWith('_vote') === (value.revisionId !== undefined));
export type ModerationInput = z.infer<typeof moderationInput>;
export class ModerationError extends Error {
  constructor(public code: 'moderation_conflict' | 'moderation_ineligible' | 'action_conflict' | 'resource_not_found') { super(code); }
}

export function moderationResource(input: ModerationInput) {
  if (input.command.endsWith('_vote')) return `vote:${input.targetId}:${input.revisionId}`;
  if (['suspend', 'unsuspend'].includes(input.command)) return `contributor:${input.targetId}`;
  if (input.command === 'resolve_report') return `report:${input.targetId}`;
  return `revision:${input.targetId}`;
}

async function savedAction(db: SqlDatabase, actionId: string) {
  return db.prepare('SELECT actor, request_json, outcome, expected_revision FROM moderation_actions WHERE action_id = ?')
    .bind(actionId).first<{ actor: string; request_json: string; outcome: string; expected_revision: number }>();
}

/** Atomically apply a reasoned operator action and preserve its exact retry outcome. */
export async function moderate(db: SqlDatabase, actor: string, value: unknown, now: number) {
  const input = moderationInput.parse(value), resource = moderationResource(input), body = JSON.stringify(input);
  const reply = (row: NonNullable<Awaited<ReturnType<typeof savedAction>>>) => {
    if (row.actor !== actor || row.request_json !== body) throw new ModerationError('action_conflict');
    if (row.outcome !== 'applied') throw new ModerationError(row.outcome === 'conflict' ? 'moderation_conflict' : 'moderation_ineligible');
    return { version: 1, actionId: input.actionId, resource, revision: row.expected_revision + 1, outcome: 'applied' };
  };
  const old = await savedAction(db, input.actionId);
  if (old) return reply(old);
  const statement = (outcome: string) => db.prepare(`INSERT INTO moderation_actions
    (action_id, actor, resource, target_id, revision_id, command, expected_revision, reason, request_json, outcome, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(input.actionId, actor, resource, input.targetId,
    input.revisionId ?? null, input.command, input.expectedRevision, input.reason, body, outcome, now * 1000);
  try { await statement('applied').run(); }
  catch (error) {
    const raced = await savedAction(db, input.actionId);
    if (raced) return reply(raced);
    const conflict = String(error).includes('moderation_conflict');
    if (!conflict && !String(error).includes('moderation_ineligible')) throw error;
    try { await statement(conflict ? 'conflict' : 'denied').run(); }
    catch { const raced = await savedAction(db, input.actionId); if (raced) return reply(raced); throw error; }
  }
  return reply((await savedAction(db, input.actionId))!);
}

/** Inspect one private resource with its optimistic-write token and bounded audit. */
export async function inspectModeration(db: SqlDatabase, kind: string, targetId: string, revisionId?: string) {
  resourceId.parse(targetId);
  if (!['revision', 'contributor', 'vote', 'report'].includes(kind) || (kind === 'vote') !== (revisionId !== undefined))
    throw new ModerationError('resource_not_found');
  if (revisionId) resourceId.parse(revisionId);
  const resource = `${kind}:${targetId}${revisionId ? `:${revisionId}` : ''}`;
  let statement: SqlStatement;
  if (kind === 'revision') statement = db.prepare(`SELECT r.revision_id AS revisionId, r.contributor_id AS contributorId,
    p.state, c.state AS authorState, s.imports, s.likes, s.dislikes FROM contribution_revisions r
    JOIN contribution_publications p USING (revision_id) JOIN contribution_statistics s USING (revision_id)
    JOIN contributors c ON c.contributor_id = r.contributor_id WHERE r.revision_id = ?`).bind(targetId);
  else if (kind === 'contributor') statement = db.prepare('SELECT contributor_id AS contributorId, display_name AS displayName, state FROM contributors WHERE contributor_id = ?')
    .bind(targetId);
  else if (kind === 'report') statement = db.prepare(`SELECT report_id AS reportId, contributor_id AS contributorId,
    revision_id AS revisionId, reason, note, state, created_at AS createdAt FROM reports WHERE report_id = ?`).bind(targetId);
  else statement = db.prepare(`SELECT v.contributor_id AS contributorId, v.revision_id AS revisionId, v.value,
    coalesce(e.excluded, 0) AS excluded FROM revision_votes v LEFT JOIN vote_exclusions e USING (contributor_id, revision_id)
    WHERE v.contributor_id = ? AND v.revision_id = ?`).bind(targetId, revisionId!);
  const [state, revision, audit] = await db.batch<Record<string, unknown>>([statement,
    db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(resource),
    db.prepare(`SELECT action_id AS actionId, actor, command, reason, outcome, expected_revision AS expectedRevision,
      created_at AS createdAt FROM moderation_actions WHERE resource = ? ORDER BY created_at DESC, action_id DESC LIMIT 20`).bind(resource)]);
  if (!state.results.length) throw new ModerationError('resource_not_found');
  return { version: 1, resource, revision: Number(revision.results[0]?.revision ?? 0), state: state.results[0], audit: audit.results };
}