import type { SqlDatabase } from '../runtime/contracts';

/** A work label: the most observed title (ties → lowest title) and its year; the ID stands in when missing. */
export interface ItemLabel {
  workId: string; title: string; year: number | null; kind: 'movie' | 'episode' | null;
  episode: { season: number; episode: number } | null;
}
export interface VersionLabel { versionId: string; label: string }
export interface FilterLabel {
  filterId: string; category: string | null; subcategory: string | null; action: string | null;
  startMs: number | null; endMs: number | null; expression?: string;
}
export interface ContributorLabel { contributorId: string; displayName: string }

/** SQL for a work's display title; `work` is a column expression such as `v.work_id`. */
export const workTitleSql = (work: string) =>
  `(SELECT t.title FROM work_titles t WHERE t.work_id = ${work} ORDER BY t.observations DESC, t.title LIMIT 1)`;
/** SQL for the year of the work's display title (-1 is stored for "unknown"). */
export const workYearSql = (work: string) =>
  `(SELECT nullif(t.year, -1) FROM work_titles t WHERE t.work_id = ${work} ORDER BY t.observations DESC, t.title LIMIT 1)`;
/**
 * SQL for a version label, `COALESCE(aliases.label, versions.label_text)`. A version with neither
 * alias nor text is "no version", which is the Theatrical alias (§3.4).
 */
export const versionLabelSql = (v: string) => `coalesce((SELECT a.label FROM aliases a WHERE a.alias_id =
  coalesce(${v}.alias_id, CASE WHEN ${v}.label_text IS NULL THEN 'theatrical' END)), ${v}.label_text, ${v}.version_id)`;

export const itemFallback = (workId: string): ItemLabel => ({ workId, title: workId, year: null, kind: null, episode: null });
export const versionFallback = (versionId: string): VersionLabel => ({ versionId, label: versionId });
export const filterFallback = (filterId: string): FilterLabel =>
  ({ filterId, category: null, subcategory: null, action: null, startMs: null, endMs: null });
export const contributorFallback = (contributorId: string): ContributorLabel => ({ contributorId, displayName: contributorId });

type Row = Record<string, any>;
const ids = (values: Iterable<string | null | undefined>) => JSON.stringify([...new Set([...values].filter((v): v is string => !!v))]);

/** Map an item row selected with `itemColumns` to its label. */
export function itemFromRow(row: Row): ItemLabel {
  return { workId: row.workId, title: row.title ?? row.workId, year: row.year ?? null, kind: row.kind ?? null,
    episode: row.season === null || row.season === undefined || row.season < 0 ? null : { season: row.season, episode: row.episode } };
}
/** Item label columns for a `works` alias `w`; pairs with `itemFromRow`. */
export const itemColumns = (w: string) => `${w}.work_id AS workId, ${workTitleSql(`${w}.work_id`)} AS title,
  ${workYearSql(`${w}.work_id`)} AS year, ${w}.kind AS kind,
  (SELECT i.season FROM work_identifiers i WHERE i.work_id = ${w}.work_id AND i.kind = 'episode' LIMIT 1) AS season,
  (SELECT i.episode FROM work_identifiers i WHERE i.work_id = ${w}.work_id AND i.kind = 'episode' LIMIT 1) AS episode`;

export function filterFromRow(row: Row): FilterLabel {
  const label: FilterLabel = { filterId: row.filterId, category: row.category ?? null, subcategory: row.subcategory ?? null,
    action: row.action ?? null, startMs: row.startMs ?? null, endMs: row.endMs ?? null };
  if (row.expression) label.expression = row.expression;
  return label;
}

/** Labels resolved in one batch; every lookup falls back to IDs for deleted or merged rows. */
export class Labels {
  constructor(private works = new Map<string, ItemLabel>(), private versions = new Map<string, VersionLabel & { workId: string }>(),
    private filters = new Map<string, FilterLabel & { versionId: string }>(), private contributors = new Map<string, ContributorLabel>(),
    private submissions = new Map<string, { versionId: string; contributorId: string }>()) {}
  item(workId: string | null | undefined): ItemLabel | null { return workId ? this.works.get(workId) ?? itemFallback(workId) : null; }
  version(versionId: string | null | undefined): VersionLabel | null {
    if (!versionId) return null;
    const row = this.versions.get(versionId);
    return row ? { versionId, label: row.label } : versionFallback(versionId);
  }
  filter(filterId: string | null | undefined): FilterLabel | null {
    if (!filterId) return null;
    const row = this.filters.get(filterId);
    if (!row) return filterFallback(filterId);
    const { versionId: _, ...label } = row;
    return label;
  }
  contributor(id: string | null | undefined): ContributorLabel | null { return id ? this.contributors.get(id) ?? contributorFallback(id) : null; }
  /** The work of a version, when the version still exists. */
  workOf(versionId: string | null | undefined) { return versionId ? this.versions.get(versionId)?.workId ?? null : null; }
  /** The version of a filter, when the filter is registered. */
  versionOf(filterId: string | null | undefined) { return filterId ? this.filters.get(filterId)?.versionId ?? null : null; }
  /** The version and author of a submission, when its acceptance exists. */
  submission(submissionId: string | null | undefined) { return submissionId ? this.submissions.get(submissionId) ?? null : null; }
  /** `{ item, version, filter }` for a filter, submission, version or work target, resolving parents. */
  target(type: 'filter' | 'submission' | 'version' | 'work', id: string) {
    const versionId = type === 'filter' ? this.versionOf(id) : type === 'submission' ? this.submission(id)?.versionId ?? null
      : type === 'version' ? id : null;
    const workId = type === 'work' ? id : this.workOf(versionId);
    return { item: this.item(workId), version: this.version(versionId), filter: type === 'filter' ? this.filter(id) : null };
  }
}

export interface LabelRequest {
  works?: Iterable<string | null | undefined>; versions?: Iterable<string | null | undefined>;
  filters?: Iterable<string | null | undefined>; contributors?: Iterable<string | null | undefined>;
  submissions?: Iterable<string | null | undefined>;
}

/**
 * Resolve item, version, filter, submission and contributor labels in one batch (at most five
 * statements). Parents are included: a filter or submission brings its version and work, a version its work.
 */
export async function resolveLabels(db: SqlDatabase, request: LabelRequest): Promise<Labels> {
  const filters = ids(request.filters ?? []), versions = ids(request.versions ?? []), works = ids(request.works ?? []);
  const submissions = ids(request.submissions ?? []), contributors = ids(request.contributors ?? []);
  const statements = [], kinds: string[] = [];
  if (filters !== '[]') {
    kinds.push('filters');
    statements.push(db.prepare(`SELECT r.filter_id AS filterId, r.version_id AS versionId, f.category,
      coalesce(f.subcategory, r.incumbent_subcategory) AS subcategory, coalesce(f.action, r.incumbent_action) AS action,
      f.start_ms AS startMs, f.end_ms AS endMs, coalesce(f.expression, r.incumbent_expression) AS expression
      FROM json_each(?) j JOIN filter_registry r ON r.filter_id = j.value LEFT JOIN master_filters f ON f.filter_id = r.filter_id`).bind(filters));
  }
  if (submissions !== '[]') {
    kinds.push('submissions');
    statements.push(db.prepare(`SELECT a.submission_id AS submissionId, a.version_id AS versionId, a.contributor_id AS contributorId
      FROM json_each(?) j JOIN version_acceptances a ON a.submission_id = j.value`).bind(submissions));
  }
  const versionSet = `SELECT value FROM json_each(?1) UNION SELECT version_id FROM filter_registry WHERE filter_id IN
    (SELECT value FROM json_each(?2)) UNION SELECT version_id FROM version_acceptances WHERE submission_id IN (SELECT value FROM json_each(?3))`;
  const anyVersion = versions !== '[]' || filters !== '[]' || submissions !== '[]';
  if (anyVersion) {
    kinds.push('versions');
    statements.push(db.prepare(`SELECT v.version_id AS versionId, v.work_id AS workId, ${versionLabelSql('v')} AS label
      FROM versions v WHERE v.version_id IN (${versionSet})`).bind(versions, filters, submissions));
  }
  if (works !== '[]' || anyVersion) {
    kinds.push('works');
    statements.push(db.prepare(`SELECT ${itemColumns('w')} FROM works w WHERE w.work_id IN (SELECT value FROM json_each(?4)
      UNION SELECT work_id FROM versions WHERE version_id IN (${versionSet}))`).bind(versions, filters, submissions, works));
  }
  if (contributors !== '[]') {
    kinds.push('contributors');
    statements.push(db.prepare(`SELECT contributor_id AS contributorId, display_name AS displayName FROM contributors
      WHERE contributor_id IN (SELECT value FROM json_each(?))`).bind(contributors));
  }
  const results = statements.length ? await db.batch<Row>(statements) : [];
  const rows = (kind: string) => results[kinds.indexOf(kind)]?.results ?? [];
  return new Labels(
    new Map(rows('works').map(row => [row.workId, itemFromRow(row)])),
    new Map(rows('versions').map(row => [row.versionId, { versionId: row.versionId, label: row.label, workId: row.workId }])),
    new Map(rows('filters').map(row => [row.filterId, { ...filterFromRow(row), versionId: row.versionId }])),
    new Map(rows('contributors').map(row => [row.contributorId, { contributorId: row.contributorId, displayName: row.displayName }])),
    new Map(rows('submissions').map(row => [row.submissionId, { versionId: row.versionId, contributorId: row.contributorId }])));
}
