import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { z } from 'zod';

export const resourceId = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
/** An attestation target: `submissionId:entryId` (§7.8). */
export const attestationId = z.string().regex(/^[A-Za-z0-9_.-]{1,128}:[A-Za-z0-9_.-]{1,32}$/);
export const privateNote = z.string().max(1000).refine(value => !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value));
export const MODERATION_COMMANDS = ['quarantine', 'restore', 'suspend', 'unsuspend', 'exclude_vote', 'restore_vote', 'resolve_report'] as const;
export const MODERATION_RESOURCES = ['filter', 'attestation', 'submission', 'contributor', 'version', 'vote', 'report'] as const;
export type ModerationCommand = typeof MODERATION_COMMANDS[number];
export type ModerationResource = typeof MODERATION_RESOURCES[number];

const allowed: Record<ModerationCommand, readonly ModerationResource[]> = {
  quarantine: ['filter', 'attestation', 'submission', 'contributor', 'version'],
  restore: ['filter', 'attestation', 'submission', 'contributor', 'version'],
  suspend: ['contributor'], unsuspend: ['contributor'], exclude_vote: ['vote'], restore_vote: ['vote'], resolve_report: ['report'],
};

/** `POST /admin/v1/moderation` envelope for the visibility, account, vote and report commands of §7.8. */
export const moderationInput = z.strictObject({ version: z.literal(1), actionId: z.string().uuid(),
  command: z.enum(MODERATION_COMMANDS), resource: z.enum(MODERATION_RESOURCES), targetId: z.string().max(161),
  expectedRevision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
  reason: privateNote.refine(value => value.trim().length > 0), contributorId: resourceId.optional(),
}).refine(value => allowed[value.command].includes(value.resource) && (value.resource === 'vote') === (value.contributorId !== undefined) &&
  (value.resource === 'attestation' ? attestationId : resourceId).safeParse(value.targetId).success);
export type ModerationInput = z.infer<typeof moderationInput>;
export type ModerationCode = 'moderation_conflict' | 'moderation_ineligible' | 'action_conflict' | 'resource_not_found' | 'invalid_moderation';
export class ModerationError extends Error {
  constructor(public code: ModerationCode) { super(code); }
}

/** Statements that apply one action; they join the audit row in a single batch. */
export type ModerationPlanner = (db: SqlDatabase, input: ModerationInput, now: number) => Promise<SqlStatement[]>;
export interface ModerationOptions {
  /** Planners for the master-set commands (quarantine, restore and votes); see `admin/master-moderation`. */
  planners?: Partial<Record<ModerationCommand, ModerationPlanner>>;
  /** True when a failed batch lost an optimistic write race and should be planned again. */
  retryable?: (error: unknown) => boolean;
}

export function moderationResource(input: Pick<ModerationInput, 'resource' | 'targetId' | 'contributorId'>) {
  return input.resource === 'vote' ? `vote:${input.targetId}:${input.contributorId}` : `${input.resource}:${input.targetId}`;
}

async function savedAction(db: SqlDatabase, actionId: string) {
  return db.prepare('SELECT actor, request_json, outcome, error_code, expected_revision FROM moderation_actions WHERE action_id = ?')
    .bind(actionId).first<{ actor: string; request_json: string; outcome: string; error_code: ModerationCode | null; expected_revision: number }>();
}

const accountPlanners: Partial<Record<ModerationCommand, ModerationPlanner>> = {
  suspend: async (db, input) => {
    const row = await db.prepare('SELECT state FROM contributors WHERE contributor_id = ?').bind(input.targetId).first<{ state: string }>();
    if (!row) throw new ModerationError('resource_not_found');
    if (row.state !== 'active') throw new ModerationError('moderation_ineligible');
    return [db.prepare(`UPDATE contributors SET state = 'suspended' WHERE contributor_id = ? AND state = 'active'`).bind(input.targetId)];
  },
  unsuspend: async (db, input) => {
    const row = await db.prepare('SELECT state FROM contributors WHERE contributor_id = ?').bind(input.targetId).first<{ state: string }>();
    if (!row) throw new ModerationError('resource_not_found');
    if (row.state !== 'suspended') throw new ModerationError('moderation_ineligible');
    return [db.prepare(`UPDATE contributors SET state = 'active' WHERE contributor_id = ? AND state = 'suspended'`).bind(input.targetId)];
  },
  resolve_report: async (db, input) => {
    const row = await db.prepare('SELECT state FROM reports WHERE report_id = ?').bind(input.targetId).first<{ state: string }>();
    if (!row) throw new ModerationError('resource_not_found');
    if (row.state !== 'open') throw new ModerationError('moderation_ineligible');
    return [db.prepare(`UPDATE reports SET state = 'resolved' WHERE report_id = ? AND state = 'open'`).bind(input.targetId)];
  },
};

/**
 * Atomically apply a reasoned operator action with its audit row and preserve its exact retry
 * outcome. A stale `expectedRevision` is audited as a conflict; refused actions are audited as denied.
 */
export async function moderate(db: SqlDatabase, actor: string, value: unknown, now: number, options: ModerationOptions = {}) {
  const input = moderationInput.parse(value), resource = moderationResource(input), body = JSON.stringify(input);
  const reply = (row: NonNullable<Awaited<ReturnType<typeof savedAction>>>) => {
    if (row.actor !== actor || row.request_json !== body) throw new ModerationError('action_conflict');
    if (row.outcome !== 'applied') throw new ModerationError(row.error_code ?? 'moderation_conflict');
    return { version: 1, actionId: input.actionId, resource, revision: row.expected_revision + 1, outcome: 'applied' };
  };
  const audit = (code: ModerationCode | null) => db.prepare(`INSERT INTO moderation_actions
    (action_id, actor, resource, target_id, command, expected_revision, reason, request_json, outcome, error_code, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(input.actionId, actor, resource, input.targetId, input.command,
    input.expectedRevision, input.reason, body, code === null ? 'applied' : code === 'moderation_conflict' ? 'conflict' : 'denied',
    code, now * 1000);
  const refuse = async (code: ModerationCode) => {
    try { await audit(code).run(); }
    catch (error) { const raced = await savedAction(db, input.actionId); if (raced) return reply(raced); throw error; }
    throw new ModerationError(code);
  };
  const planner = { ...accountPlanners, ...options.planners }[input.command];
  if (!planner) throw new ModerationError('invalid_moderation');
  for (let attempt = 1; ; attempt++) {
    const old = await savedAction(db, input.actionId);
    if (old) return reply(old);
    const current = await db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(resource).first<{ revision: number }>();
    if ((current?.revision ?? 0) !== input.expectedRevision) return refuse('moderation_conflict');
    let statements: SqlStatement[];
    try { statements = await planner(db, input, now); }
    catch (error) { if (error instanceof ModerationError) return refuse(error.code); throw error; }
    try {
      await db.batch([audit(null), ...statements]);
      return reply((await savedAction(db, input.actionId))!);
    } catch (error) {
      const raced = await savedAction(db, input.actionId);
      if (raced) return reply(raced);
      if (String(error).includes('moderation_conflict')) return refuse('moderation_conflict');
      if (attempt >= 3 || !options.retryable?.(error)) throw error;
    }
  }
}

/** Inspect one account, report or vote resource with its optimistic-write token and bounded audit. */
export async function inspectModeration(db: SqlDatabase, kind: string, targetId: string, second?: string) {
  if ((kind === 'vote') !== (second !== undefined)) throw new ModerationError('resource_not_found');
  resourceId.parse(targetId);
  if (second !== undefined) resourceId.parse(second);
  let statement: SqlStatement;
  if (kind === 'contributor') statement = db.prepare(`SELECT contributor_id AS contributorId, display_name AS displayName, state,
    quarantined FROM contributors WHERE contributor_id = ?`).bind(targetId);
  else if (kind === 'report') statement = db.prepare(`SELECT report_id AS reportId, contributor_id AS contributorId,
    target_type AS targetType, target_id AS targetId, reason, note, state, created_at AS createdAt FROM reports WHERE report_id = ?`).bind(targetId);
  else if (kind === 'vote') statement = db.prepare(`SELECT filter_id AS filterId, contributor_id AS contributorId, vote, excluded,
    updated_at AS updatedAt FROM filter_votes WHERE filter_id = ? AND contributor_id = ?`).bind(targetId, second!);
  else throw new ModerationError('resource_not_found');
  return inspection(db, statement, kind === 'vote' ? `vote:${targetId}:${second}` : `${kind}:${targetId}`);
}

/** Read one resource state with its moderation revision and the latest 20 audit rows. */
export async function inspection(db: SqlDatabase, statement: SqlStatement, resource: string) {
  const [state, revision, audit] = await db.batch<Record<string, unknown>>([statement,
    db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(resource),
    db.prepare(`SELECT action_id AS actionId, actor, command, reason, outcome, expected_revision AS expectedRevision,
      created_at AS createdAt FROM moderation_actions WHERE resource = ? ORDER BY created_at DESC, action_id DESC LIMIT 20`).bind(resource)]);
  if (!state.results.length) throw new ModerationError('resource_not_found');
  return { version: 1, resource, revision: Number(revision.results[0]?.revision ?? 0), state: state.results[0], audit: audit.results };
}
