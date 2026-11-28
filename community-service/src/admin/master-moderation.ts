import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { MasterPolicy } from '../filters/policy';
import { acceptanceQuarantineStatements, acceptanceRows } from '../versions/repository';
import { deriveVersionState } from '../versions/acceptance';
import { isCatalogWriteConflict, workWriteGuard } from '../versions/works';
import { recomputeStatements } from '../master/recompute';
import { loadVersionState, type VersionState } from '../master/state';
import { moderatedVoteEventStatement } from '../master/vote-events';
import { inspection, ModerationError, type ModerationInput, type ModerationOptions, type ModerationPlanner } from './moderation';

export interface MasterModerationContext { service: string; policy: MasterPolicy; now: number }

/**
 * Recompute each version with `change` applied, after guarding every affected work once. `guarded`
 * lists works whose guard the caller's statements already hold.
 */
export async function recomputeAffected(db: SqlDatabase, versionIds: readonly string[], context: MasterModerationContext,
  change: (state: VersionState) => void, guarded: readonly string[] = []): Promise<SqlStatement[]> {
  const rows = await db.prepare(`SELECT version_id, work_id FROM versions WHERE status != 'merged_into'
    AND version_id IN (SELECT value FROM json_each(?)) ORDER BY version_id`).bind(JSON.stringify([...new Set(versionIds)]))
    .all<{ version_id: string; work_id: string }>();
  const guards: SqlStatement[] = [];
  for (const work of new Set(rows.results.map(row => row.work_id))) if (!guarded.includes(work)) guards.push(await workWriteGuard(db, work));
  const statements: SqlStatement[] = [];
  for (const row of rows.results) {
    const state = await loadVersionState(db, row.version_id);
    if (!state) continue;
    change(state);
    statements.push(...(await recomputeStatements(db, state, context)).statements);
  }
  return [...guards, ...statements];
}

const toggle = (list: string[], id: string, on: boolean) => on ? [...new Set([...list, id])] : list.filter(item => item !== id);
const quarantining = (input: ModerationInput) => input.command === 'quarantine';
function eligible(current: boolean, input: ModerationInput) {
  if (current === quarantining(input)) throw new ModerationError('moderation_ineligible');
}

async function filterPlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const row = await db.prepare('SELECT version_id, quarantined FROM filter_registry WHERE filter_id = ?').bind(input.targetId)
    .first<{ version_id: string; quarantined: number }>();
  if (!row) throw new ModerationError('resource_not_found');
  eligible(row.quarantined === 1, input);
  return recomputeAffected(db, [row.version_id], context, state => {
    const entry = state.registry.find(item => item.filterId === input.targetId);
    if (!entry) return;
    entry.quarantined = quarantining(input);
    if (!entry.quarantined && entry.state === 'quarantined') entry.state = 'active';
  });
}

async function attestationPlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const [submissionId, entryId] = input.targetId.split(':');
  const row = await db.prepare(`SELECT a.version_id, e.quarantined FROM submission_entries e JOIN version_acceptances a USING (submission_id)
    WHERE e.submission_id = ? AND e.entry_id = ?`).bind(submissionId, entryId).first<{ version_id: string; quarantined: number }>();
  if (!row) throw new ModerationError('resource_not_found');
  eligible(row.quarantined === 1, input);
  return [db.prepare('UPDATE submission_entries SET quarantined = ? WHERE submission_id = ? AND entry_id = ?')
    .bind(Number(quarantining(input)), submissionId, entryId),
  ...await recomputeAffected(db, [row.version_id], context, state => {
    state.quarantinedAttestations = state.quarantinedAttestations.filter(item => item.submissionId !== submissionId || item.entryId !== entryId);
    if (quarantining(input)) state.quarantinedAttestations.push({ submissionId, entryId });
  })];
}

async function submissionPlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const row = await db.prepare(`SELECT a.version_id, a.quarantined, v.work_id, v.status FROM version_acceptances a
    JOIN versions v USING (version_id) JOIN submissions s USING (submission_id) WHERE a.submission_id = ?`).bind(input.targetId)
    .first<{ version_id: string; quarantined: number; work_id: string; status: string }>();
  if (!row) throw new ModerationError('resource_not_found');
  eligible(row.quarantined === 1, input);
  const on = quarantining(input), n = context.policy.candidatePromotionContributors;
  const rows = (await acceptanceRows(db, [row.version_id])).map(item => item.submissionId === input.targetId ? { ...item, quarantined: on } : item);
  const promoted = deriveVersionState(rows, row.status, n).promote;
  return [...await acceptanceQuarantineStatements(db, input.targetId, on, n),
    ...await recomputeAffected(db, [row.version_id], context, state => {
      state.quarantinedSubmissions = toggle(state.quarantinedSubmissions, input.targetId, on);
      if (promoted) state.status = 'confirmed';
    }, [row.work_id])];
}

async function contributorPlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const row = await db.prepare('SELECT quarantined FROM contributors WHERE contributor_id = ?').bind(input.targetId).first<{ quarantined: number }>();
  if (!row) throw new ModerationError('resource_not_found');
  eligible(row.quarantined === 1, input);
  const versions = await db.prepare(`SELECT version_id FROM version_acceptances WHERE contributor_id = ?1
    UNION SELECT r.version_id FROM filter_votes v JOIN filter_registry r USING (filter_id) WHERE v.contributor_id = ?1`)
    .bind(input.targetId).all<{ version_id: string }>();
  return [db.prepare('UPDATE contributors SET quarantined = ? WHERE contributor_id = ?').bind(Number(quarantining(input)), input.targetId),
    ...await recomputeAffected(db, versions.results.map(item => item.version_id), context, state => {
      state.quarantinedContributors = toggle(state.quarantinedContributors, input.targetId, quarantining(input));
    })];
}

async function versionPlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const row = await db.prepare('SELECT status, quarantined FROM versions WHERE version_id = ?').bind(input.targetId)
    .first<{ status: string; quarantined: number }>();
  if (!row) throw new ModerationError('resource_not_found');
  if (row.status === 'merged_into') throw new ModerationError('moderation_ineligible');
  eligible(row.quarantined === 1, input);
  return [db.prepare('UPDATE versions SET quarantined = ? WHERE version_id = ?').bind(Number(quarantining(input)), input.targetId),
    ...await recomputeAffected(db, [input.targetId], context, state => { state.quarantined = quarantining(input); })];
}

async function votePlan(db: SqlDatabase, input: ModerationInput, context: MasterModerationContext) {
  const row = await db.prepare(`SELECT r.version_id, v.excluded FROM filter_votes v JOIN filter_registry r USING (filter_id)
    WHERE v.filter_id = ? AND v.contributor_id = ?`).bind(input.targetId, input.contributorId!).first<{ version_id: string; excluded: number }>();
  if (!row) throw new ModerationError('resource_not_found');
  const exclude = input.command === 'exclude_vote';
  if ((row.excluded === 1) === exclude) throw new ModerationError('moderation_ineligible');
  return [db.prepare('UPDATE filter_votes SET excluded = ? WHERE filter_id = ? AND contributor_id = ?')
    .bind(Number(exclude), input.targetId, input.contributorId!),
  moderatedVoteEventStatement(db, input.targetId, input.contributorId!, exclude ? 'excluded' : 'restored', context.now),
  ...await recomputeAffected(db, [row.version_id], context, state => {
    for (const vote of state.votes) if (vote.filterId === input.targetId && vote.contributorId === input.contributorId) vote.excluded = exclude;
  })];
}

const visibility = { filter: filterPlan, attestation: attestationPlan, submission: submissionPlan, contributor: contributorPlan, version: versionPlan };

/** Moderation options with the master-set planners of §7.8: every action recomputes each affected version. */
export function masterModeration(context: MasterModerationContext): ModerationOptions {
  const quarantine: ModerationPlanner = (db, input) => {
    const plan = visibility[input.resource as keyof typeof visibility];
    if (!plan) throw new ModerationError('invalid_moderation');
    return plan(db, input, context);
  };
  const vote: ModerationPlanner = (db, input) => votePlan(db, input, context);
  return { planners: { quarantine, restore: quarantine, exclude_vote: vote, restore_vote: vote }, retryable: isCatalogWriteConflict };
}

/** Inspect a filter, attestation or submission with its moderation revision and audit. */
export async function inspectMasterResource(db: SqlDatabase, kind: 'filter' | 'attestation' | 'submission', targetId: string) {
  if (kind === 'filter') return inspection(db, db.prepare(`SELECT r.filter_id AS filterId, r.version_id AS versionId, r.state,
    r.merged_into AS mergedInto, r.quarantined, f.served, f.confidence, f.contributor_count AS contributorCount FROM filter_registry r
    LEFT JOIN master_filters f USING (filter_id) WHERE r.filter_id = ?`).bind(targetId), `filter:${targetId}`);
  if (kind === 'attestation') {
    const [submissionId, entryId] = targetId.split(':');
    return inspection(db, db.prepare(`SELECT e.submission_id AS submissionId, e.entry_id AS entryId, e.kind, e.category,
      e.start_ms AS startMs, e.end_ms AS endMs, e.weight, e.ignored_reason AS ignoredReason, e.quarantined, a.filter_id AS filterId
      FROM submission_entries e LEFT JOIN filter_attestations a USING (submission_id, entry_id)
      WHERE e.submission_id = ? AND e.entry_id = ?`).bind(submissionId, entryId ?? ''), `attestation:${targetId}`);
  }
  return inspection(db, db.prepare(`SELECT s.submission_id AS submissionId, s.contributor_id AS contributorId, s.version_id AS versionId,
    s.digest, s.state, s.tier, a.quarantined, s.created_at AS createdAt FROM submissions s JOIN version_acceptances a USING (submission_id)
    WHERE s.submission_id = ?`).bind(targetId), `submission:${targetId}`);
}
