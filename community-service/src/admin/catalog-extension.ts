import type { SqlDatabase, SqlStatement } from '../runtime/contracts';
import { catalogModerationInput, type CatalogActionPlan } from '../versions/moderation';
import { recomputeStatements } from '../master/recompute';
import { loadVersionState } from '../master/state';
import type { MasterModerationContext } from './master-moderation';

/**
 * The `moderateCatalog` extension that keeps master sets current inside the catalog action's batch
 * (§7.8). The catalog plan already guards every affected work, so these statements add no guard.
 *
 * - `merge_version` moves the source's submissions to the target and recomputes the target with the
 *   source's submissions, registry and votes, so votes and receipt eligibility follow the filter IDs.
 * - `confirm_version` and `approve_alias` recompute with the new status or alias. An approval already
 *   advances `versions.revision`, so that revision is treated as current and not bumped again.
 */
export function catalogExtension(context: MasterModerationContext, value: unknown) {
  return async (db: SqlDatabase, plan: CatalogActionPlan): Promise<SqlStatement[]> => {
    const input = catalogModerationInput.parse(value);
    if (input.command === 'reject_alias') return [];
    if (input.command === 'merge_version') {
      const [into, from] = plan.affectedVersionIds;
      const state = await loadVersionState(db, into, [from]);
      if (!state) return [];
      return [db.prepare('UPDATE submissions SET version_id = ?, tier = NULL WHERE version_id = ?').bind(into, from),
        ...(await recomputeStatements(db, state, context)).statements];
    }
    const statements: SqlStatement[] = [];
    for (const versionId of plan.affectedVersionIds) {
      const state = await loadVersionState(db, versionId);
      if (!state) continue;
      if (input.command === 'confirm_version') state.status = 'confirmed';
      else {
        const aliasId = input.aliasId ?? input.newAliasId!;
        if (input.label !== undefined) state.labels.set(aliasId, input.label);
        state.aliasId = aliasId;
        state.revision += 1;
      }
      statements.push(...(await recomputeStatements(db, state, context)).statements);
    }
    return statements;
  };
}
