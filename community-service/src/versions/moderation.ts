import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { z } from 'zod';
import type { CatalogPolicy } from '../config';
import type { AcceptanceRow } from './acceptance';
import { acceptanceRows, derivedStatements, readVersion } from './repository';

const resourceId = z.string().regex(/^[A-Za-z0-9_.-]{1,128}$/);
const aliasId = z.string().regex(/^[a-z0-9_]{1,64}$/);
const reason = z.string().max(1000).refine(value => value.trim().length > 0 && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value));
const label = z.string().refine(value => value.trim() === value && [...value].length >= 1 && [...value].length <= 64 &&
  !/[\p{Cc}\p{Cf}]/u.test(value));

export const CATALOG_COMMANDS = ['confirm_version', 'merge_version', 'approve_alias', 'reject_alias'] as const;

/** `POST /admin/v1/moderation` envelope for the version and alias commands of §7.8. */
export const catalogModerationInput = z.strictObject({
  version: z.literal(1), actionId: z.string().uuid(), command: z.enum(CATALOG_COMMANDS),
  resource: z.enum(['version', 'alias_suggestion']), targetId: resourceId,
  expectedRevision: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER), reason,
  intoVersionId: resourceId.optional(), aliasId: aliasId.optional(), newAliasId: aliasId.optional(), label: label.optional(),
}).refine(input => {
  const extras = ['intoVersionId', 'aliasId', 'newAliasId', 'label'].filter(key => input[key as keyof typeof input] !== undefined).sort().join(',');
  if (input.command === 'confirm_version' || input.command === 'reject_alias')
    return extras === '' && input.resource === (input.command === 'confirm_version' ? 'version' : 'alias_suggestion');
  if (input.command === 'merge_version') return input.resource === 'version' && extras === 'intoVersionId';
  return input.resource === 'alias_suggestion' && (extras === 'aliasId' || extras === 'label,newAliasId');
});
export type CatalogModerationInput = z.infer<typeof catalogModerationInput>;
export type CatalogModerationCode = 'moderation_conflict' | 'moderation_ineligible' | 'action_conflict' |
  'resource_not_found' | 'alias_conflict' | 'merge_invalid' | 'invalid_moderation';
export class CatalogModerationError extends Error {
  constructor(public code: CatalogModerationCode) { super(code); }
}
export interface CatalogActionPlan { statements: SqlStatement[]; affectedVersionIds: string[] }
export interface CatalogModerationOptions {
  policy: CatalogPolicy;
  /** Extra statements for the same batch, such as the master-set recompute of affected versions. */
  extend?: (db: SqlDatabase, plan: CatalogActionPlan) => Promise<SqlStatement[]>;
}

export function isCatalogCommand(value: unknown): boolean {
  return typeof value === 'object' && value !== null &&
    (CATALOG_COMMANDS as readonly unknown[]).includes((value as { command?: unknown }).command);
}

async function savedAction(db: SqlDatabase, actionId: string) {
  return db.prepare(`SELECT actor, request_json, outcome, error_code, expected_revision
    FROM catalog_moderation_actions WHERE action_id = ?`).bind(actionId)
    .first<{ actor: string; request_json: string; outcome: string; error_code: CatalogModerationCode | null; expected_revision: number }>();
}

const bumpVersion = (db: SqlDatabase, versionId: string) => db.prepare(`INSERT INTO moderation_versions VALUES (?, 1)
  ON CONFLICT(resource) DO UPDATE SET revision = revision + 1`).bind(`version:${versionId}`);

async function planMerge(db: SqlDatabase, input: CatalogModerationInput, policy: CatalogPolicy): Promise<CatalogActionPlan> {
  const source = await readVersion(db, input.targetId);
  if (!source) throw new CatalogModerationError('resource_not_found');
  const target = await readVersion(db, input.intoVersionId!);
  if (!target || target.version_id === source.version_id || target.work_id !== source.work_id ||
      source.status === 'merged_into' || target.status === 'merged_into') throw new CatalogModerationError('merge_invalid');
  const [from, into] = [source.version_id, target.version_id];
  const rows: AcceptanceRow[] = [...(await acceptanceRows(db, [from])).map(row => ({ ...row, tier: null })),
    ...await acceptanceRows(db, [into])];
  return { affectedVersionIds: [into, from], statements: [
    db.prepare('UPDATE versions SET merged_into = ? WHERE merged_into = ?').bind(into, from),
    db.prepare(`UPDATE versions SET status = 'merged_into', merged_into = ? WHERE version_id = ?`).bind(into, from),
    db.prepare(`INSERT INTO version_match_keys (version_id, type, algorithm, value, size_bytes, is_reference, observations)
      SELECT ?, type, algorithm, value, size_bytes, 0, observations FROM version_match_keys WHERE version_id = ?
      ON CONFLICT(version_id, type, algorithm, value, size_bytes) DO UPDATE SET observations = observations + excluded.observations`)
      .bind(into, from),
    db.prepare('DELETE FROM version_match_keys WHERE version_id = ?').bind(from),
    db.prepare(`UPDATE version_acceptances SET version_id = ?, tier = NULL, founder = 0, merged_from = coalesce(merged_from, ?)
      WHERE version_id = ?`).bind(into, from, from),
    ...derivedStatements(db, into, rows, target.status, policy.candidatePromotionContributors).statements,
    bumpVersion(db, into),
  ] };
}

async function planApproval(db: SqlDatabase, input: CatalogModerationInput, now: number): Promise<CatalogActionPlan> {
  const suggestion = await db.prepare('SELECT normalized, state FROM alias_suggestions WHERE suggestion_id = ?')
    .bind(input.targetId).first<{ normalized: string; state: string }>();
  if (!suggestion) throw new CatalogModerationError('resource_not_found');
  if (suggestion.state !== 'pending') throw new CatalogModerationError('moderation_ineligible');
  if (input.command === 'reject_alias') return { affectedVersionIds: [], statements: [db.prepare(`UPDATE alias_suggestions
    SET state = 'rejected' WHERE suggestion_id = ? AND state = 'pending'`).bind(input.targetId)] };
  const alias = input.aliasId ?? input.newAliasId!;
  const existing = await db.prepare('SELECT 1 AS found FROM aliases WHERE alias_id = ?').bind(alias).first();
  if (input.aliasId !== undefined && !existing) throw new CatalogModerationError('invalid_moderation');
  if ((input.newAliasId !== undefined && existing) || await db.prepare('SELECT 1 AS found FROM alias_spellings WHERE normalized = ?')
    .bind(suggestion.normalized).first()) throw new CatalogModerationError('alias_conflict');
  const affected = await db.prepare('SELECT version_id FROM versions WHERE alias_id IS NULL AND label_text = ? ORDER BY version_id')
    .bind(suggestion.normalized).all<{ version_id: string }>();
  return { affectedVersionIds: affected.results.map(row => row.version_id), statements: [
    ...(input.newAliasId === undefined ? [] : [db.prepare('INSERT INTO aliases (alias_id, label, created_at) VALUES (?, ?, ?)')
      .bind(alias, input.label!, now)]),
    db.prepare(`INSERT INTO alias_spellings (normalized, alias_id, source) VALUES (?, ?, 'moderator')`).bind(suggestion.normalized, alias),
    db.prepare(`UPDATE alias_suggestions SET state = 'approved', alias_id = ? WHERE suggestion_id = ? AND state = 'pending'`)
      .bind(alias, input.targetId),
    db.prepare('UPDATE versions SET alias_id = ? WHERE alias_id IS NULL AND label_text = ?').bind(alias, suggestion.normalized),
  ] };
}

async function planAction(db: SqlDatabase, input: CatalogModerationInput, policy: CatalogPolicy, now: number): Promise<CatalogActionPlan> {
  if (input.command === 'merge_version') return planMerge(db, input, policy);
  if (input.command !== 'confirm_version') return planApproval(db, input, now);
  const version = await readVersion(db, input.targetId);
  if (!version) throw new CatalogModerationError('resource_not_found');
  if (version.status !== 'candidate') throw new CatalogModerationError('moderation_ineligible');
  return { affectedVersionIds: [version.version_id], statements: [db.prepare(`UPDATE versions SET status = 'confirmed'
    WHERE version_id = ? AND status = 'candidate'`).bind(version.version_id)] };
}

/**
 * Apply one reasoned version or alias action (§7.8) atomically with its audit row. Retries of
 * the same `actionId` and bytes replay the stored outcome; a stale `expectedRevision` fails.
 */
export async function moderateCatalog(db: SqlDatabase, actor: string, value: unknown, now: number,
  options: CatalogModerationOptions) {
  const input = catalogModerationInput.parse(value), body = JSON.stringify(input);
  const resource = `${input.resource}:${input.targetId}`;
  const reply = (row: NonNullable<Awaited<ReturnType<typeof savedAction>>>) => {
    if (row.actor !== actor || row.request_json !== body) throw new CatalogModerationError('action_conflict');
    if (row.outcome !== 'applied') throw new CatalogModerationError(row.error_code ?? 'moderation_conflict');
    return { version: 1, actionId: input.actionId, resource, revision: row.expected_revision + 1, outcome: 'applied' };
  };
  const audit = (code: CatalogModerationCode | null) => db.prepare(`INSERT INTO catalog_moderation_actions
    (action_id, actor, resource, target_id, command, expected_revision, reason, request_json, outcome, error_code, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(input.actionId, actor, resource, input.targetId, input.command,
    input.expectedRevision, input.reason, body, code === null ? 'applied' : code === 'moderation_conflict' ? 'conflict' : 'denied',
    code, now * 1000);
  const refuse = async (code: CatalogModerationCode) => {
    try { await audit(code).run(); }
    catch (error) { const raced = await savedAction(db, input.actionId); if (raced) return reply(raced); throw error; }
    throw new CatalogModerationError(code);
  };
  const old = await savedAction(db, input.actionId);
  if (old) return reply(old);
  const current = await db.prepare('SELECT revision FROM moderation_versions WHERE resource = ?').bind(resource)
    .first<{ revision: number }>();
  if ((current?.revision ?? 0) !== input.expectedRevision) return refuse('moderation_conflict');
  let plan: CatalogActionPlan;
  try { plan = await planAction(db, input, options.policy, now); }
  catch (error) { if (error instanceof CatalogModerationError) return refuse(error.code); throw error; }
  const extra = options.extend ? await options.extend(db, plan) : [];
  try { await db.batch([audit(null), ...plan.statements, ...extra]); }
  catch (error) {
    const raced = await savedAction(db, input.actionId);
    if (raced) return reply(raced);
    if (String(error).includes('moderation_conflict')) return refuse('moderation_conflict');
    if (String(error).includes('UNIQUE constraint failed: alias')) return refuse('alias_conflict');
    throw error;
  }
  return reply((await savedAction(db, input.actionId))!);
}
