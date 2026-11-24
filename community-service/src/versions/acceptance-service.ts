import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import type { MasterPolicy } from '../filters/policy';
import { decideVersion, type AcceptanceRow, type Disposition } from './acceptance';
import { KEY_TIER, storedKey, type EvidenceKey, type EvidenceTier } from './keys';
import { aliasExists, resolveText, spellingLookup, type Label } from './labels';
import { acceptanceRows, derivedStatements, serviceId, workVersions } from './repository';
import { isCatalogWriteConflict, planWork, VersionAcceptanceError, type SubmissionWork, type WorkPlan } from './works';

export { isCatalogWriteConflict, VersionAcceptanceError, type SubmissionWork } from './works';

/** The parts of a validated `community-artifact-3` submission that version acceptance uses. */
export interface VersionAcceptanceInput {
  submissionId: string;
  contributorId: string;
  work: SubmissionWork;
  version: { choice: 'alias'; aliasId: string } | { choice: 'other'; text: string | null };
  durationMs: number;
  matchKeys: readonly EvidenceKey[];
}
export interface AliasSuggestion { suggestionId: string; text: string; state: 'pending' }
export interface VersionAcceptance {
  workId: string;
  versionId: string;
  versionStatus: 'confirmed' | 'candidate';
  disposition: Disposition;
  /** The attach tier, or null when this submission created the version. */
  tier: '1' | '2' | '3' | null;
  promoted: boolean;
  identifierConflicts: number;
  label: Label;
  aliasSuggestion: AliasSuggestion | null;
}
export interface AcceptanceOptions {
  policy: Pick<MasterPolicy, 'candidatePromotionContributors'>;
  /** Unix seconds. */
  now: number;
  newId?: (prefix: string) => string;
}
export interface AcceptancePlan {
  result: VersionAcceptance; statements: SqlStatement[];
  /** The work writes of this plan, so a master snapshot can be summarized before the batch commits. */
  work: Pick<WorkPlan, 'created' | 'attached' | 'title'> & { kind: SubmissionWork['kind'] };
}

/**
 * Run D13 version acceptance (§3.5) and return the result with the write statements. The
 * submissions service appends these statements to its own submission batch so acceptance
 * commits atomically with the stored submission. The first statement is a work write guard,
 * so derived runtime, trust and promotion computed from this plan's reads can only commit
 * when no concurrent catalog write intervened. When the batch fails with an error for which
 * `isCatalogWriteConflict` is true, plan again and retry. `POST /v1/submissions` is the only writer.
 */
export async function planVersionAcceptance(db: SqlDatabase, input: VersionAcceptanceInput,
  options: AcceptanceOptions): Promise<AcceptancePlan> {
  const newId = options.newId ?? serviceId, now = options.now;
  const keys = input.matchKeys.map(storedKey);
  const work = await planWork(db, input.work, input.submissionId, now, newId);
  let label: Label, suggestionText: string | null = null;
  if (input.version.choice === 'alias') {
    if (!await aliasExists(db, input.version.aliasId)) throw new VersionAcceptanceError('unknown_alias');
    label = { aliasId: input.version.aliasId, labelText: null };
  } else {
    label = await resolveText(input.version.text, spellingLookup(db));
    if (label.labelText !== null && label.aliasId === null) suggestionText = input.version.text!;
  }
  const versions = work.created ? [] : await workVersions(db, work.workId);
  const decision = decideVersion({ choice: input.version.choice, label, keys }, versions);
  const statements = [...work.statements];
  let versionId: string, tier: EvidenceTier | null, status: 'confirmed' | 'candidate', previous: AcceptanceRow[] = [];
  if (decision.disposition === 'attached') {
    versionId = decision.versionId;
    tier = decision.tier;
    status = versions.find(version => version.versionId === versionId)!.status as 'confirmed' | 'candidate';
    previous = await acceptanceRows(db, [versionId]);
    for (const key of keys) statements.push(db.prepare(`INSERT INTO version_match_keys
      (version_id, type, algorithm, value, size_bytes, is_reference, observations) VALUES (?, ?, ?, ?, ?, 0, 1)
      ON CONFLICT(version_id, type, algorithm, value, size_bytes) DO UPDATE SET observations = observations + 1`)
      .bind(versionId, key.type, key.algorithm, key.value, key.sizeBytes));
  } else {
    versionId = newId('v');
    tier = keys.length ? Math.min(...keys.map(key => KEY_TIER[key.type])) as EvidenceTier : null;
    status = decision.disposition === 'created_confirmed' ? 'confirmed' : 'candidate';
    statements.push(db.prepare(`INSERT INTO versions (version_id, work_id, alias_id, label_text, status, merged_into,
      runtime_ms, trust, revision, reference_submission_id, quarantined, created_seq, created_at)
      VALUES (?, ?, ?, ?, ?, NULL, ?, 0, 1, ?, 0, (SELECT coalesce(max(created_seq), 0) + 1 FROM versions), ?)`)
      .bind(versionId, work.workId, label.aliasId, label.labelText, status, input.durationMs, input.submissionId, now));
    for (const key of keys) statements.push(db.prepare(`INSERT INTO version_match_keys
      (version_id, type, algorithm, value, size_bytes, is_reference, observations) VALUES (?, ?, ?, ?, ?, 1, 1)`)
      .bind(versionId, key.type, key.algorithm, key.value, key.sizeBytes));
  }
  statements.push(db.prepare(`INSERT INTO version_acceptances (submission_id, version_id, contributor_id, accepted_seq,
    tier, founder, duration_ms, quarantined, merged_from, accepted_at)
    VALUES (?, ?, ?, (SELECT coalesce(max(accepted_seq), 0) + 1 FROM version_acceptances), ?, ?, ?, 0, NULL, ?)`)
    .bind(input.submissionId, versionId, input.contributorId, tier, Number(decision.disposition !== 'attached'),
      input.durationMs, now));
  const latest = Math.max(0, ...previous.map(row => row.acceptedSeq)) + 1;
  const derived = derivedStatements(db, versionId, [...previous, { contributorId: input.contributorId, acceptedSeq: latest,
    tier, durationMs: input.durationMs, quarantined: false }], status, options.policy.candidatePromotionContributors);
  statements.push(...derived.statements);
  let aliasSuggestion: AliasSuggestion | null = null;
  if (suggestionText !== null) {
    aliasSuggestion = { suggestionId: newId('as'), text: suggestionText, state: 'pending' };
    statements.push(db.prepare(`INSERT INTO alias_suggestions (suggestion_id, normalized, text, submission_id, state, alias_id, created_at)
      VALUES (?, ?, ?, ?, 'pending', NULL, ?)`)
      .bind(aliasSuggestion.suggestionId, label.labelText, suggestionText, input.submissionId, now));
  }
  return { statements, work: { created: work.created, attached: work.attached, title: work.title, kind: input.work.kind },
    result: { workId: work.workId, versionId, versionStatus: derived.promoted ? 'confirmed' : status,
    disposition: decision.disposition, tier: decision.disposition === 'attached' ? String(tier) as '1' | '2' | '3' : null,
    promoted: derived.promoted, identifierConflicts: work.conflicts, label, aliasSuggestion } };
}
