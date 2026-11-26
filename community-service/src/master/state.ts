import type { SqlDatabase } from '../runtime/contracts';
import type { EntryInput, MatchKey, MergeInput, RegistryEntry, SnapshotHeader, SubmissionInput, VoteInput } from '../filters';
import { readWorkRows, summarizeWorks, type WorkRows } from '../versions/work-summary';

/** A version's master-set inputs (§6) as read from the store, before any pending change is applied. */
export interface VersionState {
  versionId: string;
  workId: string;
  status: 'confirmed' | 'candidate';
  aliasId: string | null;
  labelText: string | null;
  quarantined: boolean;
  /** `versions.revision`; may be ahead of the latest stored snapshot after an alias relabel. */
  revision: number;
  /** The latest stored snapshot revision and its served content, if any. */
  latest: { revision: number; servedContent: string | null } | null;
  workRows: WorkRows;
  /** Display labels by alias ID. */
  labels: Map<string, string>;
  referenceKeys: MatchKey[];
  submissions: SubmissionInput[];
  votes: VoteInput[];
  registry: RegistryEntry[];
  quarantinedContributors: string[];
  quarantinedSubmissions: string[];
  quarantinedAttestations: { submissionId: string; entryId: string }[];
  /** Every version whose submissions, registry and votes were read: this version plus absorbed ones. */
  sourceVersionIds: string[];
}

type Row = Record<string, unknown>;
const decoder = new TextDecoder();

/** Rebuild served content (§6.9) from stored snapshot bytes by removing `revision` and `generatedAt`. */
export function servedContentOf(bytes: ArrayBuffer | null): string | null {
  if (!bytes) return null;
  const { revision: _revision, generatedAt: _generatedAt, ...content } = JSON.parse(decoder.decode(bytes)) as Record<string, unknown>;
  return JSON.stringify(content);
}

/**
 * Read every §6 input of `versionId`. `absorb` lists versions being merged into it by the same
 * batch, whose submissions, registry and votes join this version's inputs (§7.8 `merge_version`).
 */
export async function loadVersionState(db: SqlDatabase, versionId: string, absorb: readonly string[] = []): Promise<VersionState | null> {
  const version = await db.prepare(`SELECT version_id, work_id, alias_id, label_text, status, revision, quarantined
    FROM versions WHERE version_id = ?`).bind(versionId).first<Row>();
  if (!version || version.status === 'merged_into') return null;
  const ids = JSON.stringify([versionId, ...absorb]);
  const [references, submissions, entries, coverage, registry, votes, quarantined, latest, labels] = await db.batch<Row>([
    db.prepare(`SELECT type, algorithm, value, size_bytes FROM version_match_keys WHERE version_id = ? AND is_reference = 1`).bind(versionId),
    db.prepare(`SELECT s.submission_id, s.digest, s.contributor_id, a.accepted_seq, a.duration_ms, a.quarantined, s.match_keys_json
      FROM version_acceptances a JOIN submissions s USING (submission_id) WHERE a.version_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT e.submission_id, e.entry_id, e.kind, e.proposed_filter_id, e.start_ms, e.end_ms, e.category, e.subcategory,
      e.severity, e.action, e.expression, e.origin, e.locally_verified, e.quarantined, f.prior_filter_id FROM submission_entries e
      JOIN version_acceptances a USING (submission_id)
      LEFT JOIN filter_attestations f ON f.submission_id = e.submission_id AND f.entry_id = e.entry_id
      WHERE a.version_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT c.submission_id, c.category, c.ordinal, c.start_ms, c.end_ms FROM submission_coverage c
      JOIN version_acceptances a USING (submission_id) WHERE a.version_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT filter_id, created_seq, incumbent_subcategory, incumbent_action, incumbent_expression, state, merged_into, quarantined
      FROM filter_registry WHERE version_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT v.filter_id, v.contributor_id, v.vote, v.excluded FROM filter_votes v JOIN filter_registry r USING (filter_id)
      WHERE r.version_id IN (SELECT value FROM json_each(?))`).bind(ids),
    db.prepare(`SELECT contributor_id FROM contributors WHERE quarantined = 1 AND contributor_id IN (
      SELECT contributor_id FROM version_acceptances WHERE version_id IN (SELECT value FROM json_each(?1))
      UNION SELECT v.contributor_id FROM filter_votes v JOIN filter_registry r USING (filter_id)
        WHERE r.version_id IN (SELECT value FROM json_each(?1)))`).bind(ids),
    db.prepare(`SELECT revision, snapshot_bytes FROM master_revisions WHERE version_id = ? ORDER BY revision DESC LIMIT 1`).bind(versionId),
    db.prepare('SELECT alias_id, label FROM aliases'),
  ]);
  const byEntry = new Map<string, EntryInput[]>(), byCoverage = new Map<string, Map<string, { startMs: number; endMs: number }[]>>();
  const quarantinedAttestations: VersionState['quarantinedAttestations'] = [];
  for (const row of entries.results) {
    const submissionId = row.submission_id as string;
    if (row.quarantined === 1) quarantinedAttestations.push({ submissionId, entryId: row.entry_id as string });
    const list = byEntry.get(submissionId) ?? byEntry.set(submissionId, []).get(submissionId)!;
    list.push({ entryId: row.entry_id as string, kind: row.kind as EntryInput['kind'], proposedFilterId: row.proposed_filter_id as string | null,
      startMs: row.start_ms as number, endMs: row.end_ms as number, category: row.category as string,
      subcategory: row.subcategory as string | null, severity: row.severity as EntryInput['severity'],
      action: row.action as EntryInput['action'], expression: (row.expression as string | null) ?? null, origin: row.origin as EntryInput['origin'],
      locallyVerified: row.locally_verified === 1, priorFilterId: (row.prior_filter_id as string | null) ?? null });
  }
  for (const row of [...coverage.results].sort((a, b) => (a.ordinal as number) - (b.ordinal as number))) {
    const categories = byCoverage.get(row.submission_id as string) ?? byCoverage.set(row.submission_id as string, new Map()).get(row.submission_id as string)!;
    const list = categories.get(row.category as string) ?? categories.set(row.category as string, []).get(row.category as string)!;
    list.push({ startMs: row.start_ms as number, endMs: row.end_ms as number });
  }
  const latestRow = latest.results[0];
  return {
    versionId, workId: version.work_id as string, status: version.status as VersionState['status'],
    aliasId: version.alias_id as string | null, labelText: version.label_text as string | null,
    quarantined: version.quarantined === 1, revision: version.revision as number,
    latest: latestRow ? { revision: latestRow.revision as number, servedContent: servedContentOf(latestRow.snapshot_bytes as ArrayBuffer | null) } : null,
    workRows: await readWorkRows(db, [version.work_id as string]),
    labels: new Map(labels.results.map(row => [row.alias_id as string, row.label as string])),
    referenceKeys: references.results.map(storedMatchKey),
    submissions: submissions.results.map(row => ({ submissionId: row.submission_id as string, digest: row.digest as string,
      contributorId: row.contributor_id as string, acceptedSeq: row.accepted_seq as number, durationMs: row.duration_ms as number,
      matchKeys: JSON.parse(row.match_keys_json as string) as MatchKey[],
      coverage: [...(byCoverage.get(row.submission_id as string) ?? new Map()).entries()].map(([category, intervals]) => ({ category, intervals })),
      entries: byEntry.get(row.submission_id as string) ?? [] })),
    votes: votes.results.map(row => ({ filterId: row.filter_id as string, contributorId: row.contributor_id as string,
      vote: row.vote as VoteInput['vote'], excluded: row.excluded === 1 })),
    registry: registry.results.map(row => ({ filterId: row.filter_id as string, createdSeq: row.created_seq as number,
      incumbentSubcategory: row.incumbent_subcategory as string | null, incumbentAction: row.incumbent_action as RegistryEntry['incumbentAction'],
      incumbentExpression: (row.incumbent_expression as string | null) ?? null,
      state: row.state as RegistryEntry['state'], mergedInto: row.merged_into as string | null, quarantined: row.quarantined === 1 })),
    quarantinedContributors: quarantined.results.map(row => row.contributor_id as string),
    quarantinedSubmissions: submissions.results.filter(row => row.quarantined === 1).map(row => row.submission_id as string),
    quarantinedAttestations, sourceVersionIds: [versionId, ...absorb],
  };
}

function storedMatchKey(row: Row): MatchKey {
  return row.algorithm ? { type: row.type as string, algorithm: row.algorithm as string, value: row.value as string }
    : { type: row.type as string, value: row.value as string, sizeBytes: row.size_bytes as number };
}

/** The snapshot header (§7.3) of a version state: service, work summary and version information. */
export function snapshotHeader(state: VersionState, service: string): SnapshotHeader {
  const work = summarizeWorks(state.workRows).get(state.workId);
  if (!work) throw new Error('work_not_found');
  return { service, work, versionInfo: { status: state.status, aliasId: state.aliasId,
    label: state.aliasId !== null ? state.labels.get(state.aliasId) ?? null : state.labelText, labelText: state.labelText } };
}

/** Build the merge input of a version state. */
export function mergeInput(state: VersionState, service: string): MergeInput {
  const stored = state.latest !== null && state.latest.revision === state.revision ? state.latest.servedContent : null;
  return {
    versionId: state.versionId, header: snapshotHeader(state, service), referenceKeys: state.referenceKeys,
    submissions: state.submissions, votes: state.votes, registry: state.registry,
    moderation: { versionQuarantined: state.quarantined, quarantinedContributors: state.quarantinedContributors,
      quarantinedSubmissions: state.quarantinedSubmissions, quarantinedAttestations: state.quarantinedAttestations },
    // A revision without a stored snapshot (new, or relabeled after its last snapshot) is current: store it unbumped.
    previous: state.latest === null && state.revision === 1 ? null : { revision: state.revision, servedContent: stored },
  };
}
