import type { SqlDatabase } from '../../runtime/contracts';
import type { ContributorLabel, FilterLabel, ItemLabel, LabelRequest, Labels, VersionLabel } from '../labels';

/** A parsed moderation resource such as `filter:f1`, `vote:f1:c1` or `attestation:s1:e1`. */
export interface ResourceRef { type: string; id: string | null; second: string | null }

export function parseResource(resource: string): ResourceRef {
  const [type, id = null, second = null] = resource.split(':');
  return { type, id, second };
}

/** Readable labels for an audited resource; unknown kinds and missing rows keep their IDs. */
export interface ResourceLabel {
  type: string; id: string | null; text: string; item: ItemLabel | null; version: VersionLabel | null;
  filter: FilterLabel | null; contributor: ContributorLabel | null;
}

/** Report targets and alias-suggestion submissions that resource labels resolve through. */
export interface Indirect {
  reports: Map<string, { targetType: 'filter' | 'version'; targetId: string; contributorId: string }>;
  suggestions: Map<string, { submissionId: string; text: string }>;
}

/** Load the report and alias-suggestion rows behind `refs` in one batch (or none). */
export async function loadIndirect(db: SqlDatabase, refs: ResourceRef[]): Promise<Indirect> {
  const reports = refs.filter(ref => ref.type === 'report' && ref.id).map(ref => ref.id!);
  const suggestions = refs.filter(ref => ref.type === 'alias_suggestion' && ref.id).map(ref => ref.id!);
  const result: Indirect = { reports: new Map(), suggestions: new Map() };
  if (!reports.length && !suggestions.length) return result;
  const [reportRows, suggestionRows] = await db.batch<Record<string, any>>([
    db.prepare(`SELECT report_id AS id, target_type AS targetType, target_id AS targetId, contributor_id AS contributorId
      FROM reports WHERE report_id IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(reports)),
    db.prepare(`SELECT suggestion_id AS id, submission_id AS submissionId, text FROM alias_suggestions
      WHERE suggestion_id IN (SELECT value FROM json_each(?))`).bind(JSON.stringify(suggestions))]);
  for (const row of reportRows.results) result.reports.set(row.id, row as any);
  for (const row of suggestionRows.results) result.suggestions.set(row.id, row as any);
  return result;
}

/** The label IDs needed to describe `refs`. */
export function resourceLabelRequest(refs: ResourceRef[], indirect: Indirect): Required<LabelRequest> {
  const request = { works: [] as string[], versions: [] as string[], filters: [] as string[], contributors: [] as string[],
    submissions: [] as string[] };
  for (const ref of refs) {
    if (!ref.id) continue;
    if (ref.type === 'filter') request.filters.push(ref.id);
    else if (ref.type === 'vote') { request.filters.push(ref.id); if (ref.second) request.contributors.push(ref.second); }
    else if (ref.type === 'version') request.versions.push(ref.id);
    else if (ref.type === 'contributor') request.contributors.push(ref.id);
    else if (ref.type === 'submission' || ref.type === 'attestation') request.submissions.push(ref.id);
    else if (ref.type === 'report') {
      const report = indirect.reports.get(ref.id);
      if (report) (report.targetType === 'filter' ? request.filters : request.versions).push(report.targetId);
    } else if (ref.type === 'alias_suggestion') {
      const suggestion = indirect.suggestions.get(ref.id);
      if (suggestion) request.submissions.push(suggestion.submissionId);
    }
  }
  return request;
}

const clock = (value: number | null) => {
  if (value === null) return '?';
  const total = Math.floor(value / 1000), h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};
/** Plain text such as `Violence › Gore 01:02:10–01:02:31`; falls back to the filter ID. */
export function filterText(filter: FilterLabel) {
  if (!filter.category) return filter.filterId;
  return `${filter.category}${filter.subcategory ? ` › ${filter.subcategory}` : ''} ${clock(filter.startMs)}–${clock(filter.endMs)}`;
}
export const itemText = (item: ItemLabel) => item.year ? `${item.title} (${item.year})` : item.title;

/** Describe one resource with labels from `resolveLabels(db, resourceLabelRequest(...))`. */
export function resourceLabel(ref: ResourceRef, labels: Labels, indirect: Indirect): ResourceLabel {
  let target = { item: null as ItemLabel | null, version: null as VersionLabel | null, filter: null as FilterLabel | null };
  let contributor: ContributorLabel | null = null, prefix = '';
  const id = ref.id;
  if (id && (ref.type === 'filter' || ref.type === 'vote')) target = labels.target('filter', id);
  else if (id && ref.type === 'version') target = labels.target('version', id);
  else if (id && (ref.type === 'submission' || ref.type === 'attestation')) {
    target = labels.target('submission', id);
    contributor = labels.contributor(labels.submission(id)?.contributorId);
    prefix = `${ref.type === 'attestation' ? 'Entry of upload' : 'Upload'}${contributor ? ` by ${contributor.displayName}` : ''}`;
  } else if (id && ref.type === 'contributor') contributor = labels.contributor(id);
  else if (id && ref.type === 'report') {
    const report = indirect.reports.get(id);
    if (report) target = labels.target(report.targetType, report.targetId);
    prefix = 'Report';
  } else if (id && ref.type === 'alias_suggestion') {
    const suggestion = indirect.suggestions.get(id);
    if (suggestion) target = labels.target('submission', suggestion.submissionId);
    prefix = suggestion ? `Alias “${suggestion.text}”` : 'Alias suggestion';
  }
  if (ref.type === 'vote' && ref.second) {
    contributor = labels.contributor(ref.second);
    prefix = `Vote by ${contributor!.displayName}`;
  }
  const parts = [prefix, target.filter ? filterText(target.filter) : '', target.item ? itemText(target.item) : '',
    target.version?.label ?? '', ref.type === 'contributor' && contributor ? contributor.displayName : ''].filter(Boolean);
  const text = parts.length ? parts.join(' · ') : [ref.type, id, ref.second].filter(Boolean).join(' ');
  return { type: ref.type, id: ref.type === 'attestation' && ref.second ? `${id}:${ref.second}` : id, text, ...target, contributor };
}
