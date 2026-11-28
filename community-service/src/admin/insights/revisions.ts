import type { SqlDatabase } from '../../runtime/contracts';
import type { ContributorLabel } from '../labels';

export type RevisionCause =
  | { kind: 'upload'; contributor: ContributorLabel }
  | { kind: 'moderation'; actor: string; command: string }
  | { kind: 'votes'; contributor: ContributorLabel };

/** What changed in a revision against the previous one; counts are null when neither snapshot nor filter IDs survive. */
export interface RevisionSummary {
  versionId: string; revision: number; generatedAt: number; filters: number | null;
  added: number | null; removed: number | null; retimed: number | null; recategorised: number | null;
  cause: RevisionCause | null;
}

type SnapshotFilter = { filterId: string; category: string; subcategory: string; startMs: number; endMs: number };
type Target = { versionId: string; revision: number; generatedAt: number };

function decode(bytes: unknown): Map<string, SnapshotFilter> | null {
  if (!bytes) return null;
  try {
    const parsed = JSON.parse(new TextDecoder().decode(new Uint8Array(bytes as ArrayBuffer))) as { filters: SnapshotFilter[] };
    return new Map(parsed.filters.map(filter => [filter.filterId, filter]));
  } catch { return null; }
}

function diff(current: Map<string, SnapshotFilter> | null, previous: Map<string, SnapshotFilter> | null | 'none',
  currentIds: Set<string> | null, previousIds: Set<string> | null | 'none') {
  if (current && previous === 'none') return { filters: current.size, added: current.size, removed: 0, retimed: 0, recategorised: 0 };
  if (current && previous && previous !== 'none') {
    let added = 0, retimed = 0, recategorised = 0;
    for (const [id, filter] of current) {
      const old = previous.get(id);
      if (!old) added++;
      else {
        if (old.startMs !== filter.startMs || old.endMs !== filter.endMs) retimed++;
        if (old.category !== filter.category || old.subcategory !== filter.subcategory) recategorised++;
      }
    }
    const removed = [...previous.keys()].filter(id => !current.has(id)).length;
    return { filters: current.size, added, removed, retimed, recategorised };
  }
  const ids = current ? new Set(current.keys()) : currentIds;
  const before = previous === 'none' ? new Set<string>() : previous ? new Set(previous.keys()) : previousIds === 'none' ? new Set<string>() : previousIds;
  if (ids && before) return { filters: ids.size, added: [...ids].filter(id => !before.has(id)).length,
    removed: [...before].filter(id => !ids.has(id)).length, retimed: null, recategorised: null };
  return { filters: ids?.size ?? null, added: null, removed: null, retimed: null, recategorised: null };
}

/**
 * SQL predicate: audit row `m` (either audit table) is an action that can change version `v`'s master set.
 * Targets are decoded from the resource and target ID: the version itself or a merge into it, its
 * filters and their votes, its submissions and attestations (`submissionId:entryId`), a contributor
 * with an upload to it, an alias suggestion from an upload to it or naming its label, and recomputes
 * of it or of all versions. Report resolutions and backup commands never change a master set.
 */
export const affects = (m: string, version: string, v = `(${version})`) => `(${m}.command NOT IN ('resolve_report', 'backup_configure', 'backup_run', 'backup_stage') AND (
  ${m}.resource = 'version:' || ${v} OR json_valid(${m}.request_json) AND ${m}.request_json ->> '$.intoVersionId' = ${v}
  OR (${m}.resource GLOB 'filter:*' OR ${m}.resource GLOB 'vote:*') AND EXISTS (SELECT 1 FROM filter_registry fr
    WHERE fr.filter_id = ${m}.target_id AND fr.version_id = ${v})
  OR (${m}.resource GLOB 'submission:*' OR ${m}.resource GLOB 'attestation:*') AND EXISTS (SELECT 1 FROM version_acceptances va
    WHERE va.submission_id = CASE WHEN instr(${m}.target_id, ':') THEN substr(${m}.target_id, 1, instr(${m}.target_id, ':') - 1)
      ELSE ${m}.target_id END AND va.version_id = ${v})
  OR ${m}.resource GLOB 'contributor:*' AND EXISTS (SELECT 1 FROM version_acceptances va
    WHERE va.contributor_id = ${m}.target_id AND va.version_id = ${v})
  OR ${m}.resource GLOB 'alias_suggestion:*' AND EXISTS (SELECT 1 FROM alias_suggestions s
    JOIN version_acceptances va ON va.submission_id = s.submission_id JOIN versions sv ON sv.version_id = va.version_id
    JOIN versions tv ON tv.version_id = ${v} WHERE s.suggestion_id = ${m}.target_id
      AND (va.version_id = ${v} OR (tv.work_id = sv.work_id AND tv.label_text = s.normalized)))
  OR ${m}.command = 'recompute' AND (${m}.target_id = 'all' OR json_valid(${m}.request_json) AND EXISTS (SELECT 1
    FROM json_each(${m}.request_json, '$.versionIds') WHERE value = ${v}))))`;

/**
 * Summarise revisions (generatedAt in Unix seconds) in three statements: snapshot bytes of each
 * revision and its predecessor, retained `master_revision_filters` IDs as a fallback, and the cause.
 * The cause is the upload, applied operator action affecting the version (see `affects`), or vote
 * recorded in the same second as the revision, preferring that order; none found yields null.
 */
export async function summariseRevisions(db: SqlDatabase, targets: Target[]): Promise<RevisionSummary[]> {
  if (!targets.length) return [];
  const pairs = JSON.stringify(targets.flatMap(t => [{ v: t.versionId, r: t.revision }, { v: t.versionId, r: t.revision - 1 }]));
  const keyed = JSON.stringify(targets.map(t => ({ v: t.versionId, g: t.generatedAt })));
  const [snapshots, retained, causes] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT version_id AS v, revision AS r, snapshot_bytes AS bytes FROM master_revisions
      WHERE (version_id, revision) IN (SELECT value ->> '$.v', value ->> '$.r' FROM json_each(?))`).bind(pairs),
    db.prepare(`SELECT version_id AS v, revision AS r, filter_id AS f FROM master_revision_filters
      WHERE (version_id, revision) IN (SELECT value ->> '$.v', value ->> '$.r' FROM json_each(?))`).bind(pairs),
    db.prepare(`SELECT j.key AS k, 1 AS p, 'upload' AS kind, s.contributor_id AS who, NULL AS command, c.display_name AS name
        FROM json_each(?1) j JOIN submissions s ON s.version_id = j.value ->> '$.v' AND s.created_at = j.value ->> '$.g'
        LEFT JOIN contributors c ON c.contributor_id = s.contributor_id
      UNION ALL SELECT j.key, 2, 'moderation', m.actor, m.command, NULL FROM json_each(?1) j JOIN moderation_actions m
        ON m.created_at BETWEEN (j.value ->> '$.g') * 1000 AND (j.value ->> '$.g') * 1000 + 999 AND m.outcome = 'applied'
        AND ${affects('m', "j.value ->> '$.v'")}
      UNION ALL SELECT j.key, 2, 'moderation', m.actor, m.command, NULL FROM json_each(?1) j JOIN catalog_moderation_actions m
        ON m.created_at BETWEEN (j.value ->> '$.g') * 1000 AND (j.value ->> '$.g') * 1000 + 999 AND m.outcome = 'applied'
        AND ${affects('m', "j.value ->> '$.v'")}
      UNION ALL SELECT j.key, 3, 'votes', e.contributor_id, NULL, c.display_name FROM json_each(?1) j
        JOIN vote_events e ON e.created_at = j.value ->> '$.g'
        JOIN filter_registry r ON r.filter_id = e.filter_id AND r.version_id = j.value ->> '$.v'
        LEFT JOIN contributors c ON c.contributor_id = e.contributor_id
      ORDER BY k, p`).bind(keyed)]);
  const bytes = new Map(snapshots.results.map(row => [`${row.v}#${row.r}`, row.bytes]));
  const ids = new Map<string, Set<string>>();
  for (const row of retained.results) {
    const key = `${row.v}#${row.r}`;
    if (!ids.has(key)) ids.set(key, new Set());
    ids.get(key)!.add(row.f);
  }
  const cause = new Map<number, RevisionCause>();
  for (const row of causes.results) {
    if (cause.has(row.k)) continue;
    const contributor = { contributorId: row.who, displayName: row.name ?? row.who };
    cause.set(row.k, row.kind === 'moderation' ? { kind: 'moderation', actor: row.who, command: row.command }
      : { kind: row.kind, contributor });
  }
  return targets.map((target, index) => {
    const current = `${target.versionId}#${target.revision}`, previous = `${target.versionId}#${target.revision - 1}`;
    const first = target.revision === 1;
    return { versionId: target.versionId, revision: target.revision, generatedAt: target.generatedAt * 1000,
      ...diff(decode(bytes.get(current)), first ? 'none' : decode(bytes.get(previous)), ids.get(current) ?? null,
        first ? 'none' : ids.get(previous) ?? null),
      cause: cause.get(index) ?? null };
  });
}
