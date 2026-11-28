import { readOperatorPage, type OperatorList, type OperatorResource, type OperatorRow } from "../../../api/api-community-operator";
import { loadDetail } from "../inspect/inspect-page";
import { InspectionPanel } from "../inspect/inspection-panel";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { PageHead } from "../shared/ui";
import { operatorLink } from "../routes";
import { aliasColumns, RecordTable, reportColumns, versionColumns, type Column } from "./record-tables";

interface RecordView { list: OperatorList; title: string; intro: string; kind: OperatorResource; columns: Column[]; filters?: [string, string][];
  id: (row: OperatorRow) => string }
/** The browsable record lists kept from the original console: every version, alias suggestion and report. */
export const recordViews: Record<"versions" | "aliases" | "reports", RecordView> = {
  versions: { list: "versions", title: "All versions", intro: "Every version of every work, by status. Open one for its filters, submissions and actions.",
    kind: "version", columns: versionColumns, id: row => String(row.versionId),
    filters: [["all", "All"], ["candidate", "Candidate"], ["confirmed", "Confirmed"], ["merged_into", "Merged"]] },
  aliases: { list: "aliases", title: "All alias suggestions", intro: "Every alias suggestion and its decision. Pending ones also appear in the queue.",
    kind: "alias_suggestion", columns: aliasColumns, id: row => String(row.suggestionId),
    filters: [["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "All"]] },
  reports: { list: "reports", title: "All reports", intro: "Open contributor reports with their private notes. Resolved reports leave this list; their audit stays.",
    kind: "report", columns: reportColumns, id: row => String(row.reportId) },
};

/** A paged record list with status chips; `selected` opens the record's inspection and actions below the list. */
export function RecordsPage({ view, params }: { view: keyof typeof recordViews; params: Record<string, string | null> }) {
  const { onAccessFailure, refreshCounts } = useOperator();
  const config = recordViews[view];
  const filter = config.filters?.some(([value]) => value === params.filter) ? params.filter! : config.filters?.[0][0] ?? null;
  const after = params.after ?? null, selected = params.selected ?? null;
  const page = useOperatorData(`records:${view}:${filter}:${after}`, signal =>
    readOperatorPage(config.list, after, signal, undefined, filter === "all" ? null : filter));
  const inspectParams = { ...params, kind: config.kind };
  const detail = useOperatorData(`records:${view}:selected:${JSON.stringify(params)}`, signal => loadDetail(inspectParams, signal), !!selected);
  const link = (values: Record<string, string | null>) => operatorLink(view, { filter, after, ...values });
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="Records" title={config.title} intro={config.intro}>
      {config.filters && <nav className="actions" aria-label={`${config.title} filter`}>{config.filters.map(([value, label]) =>
        <a key={value} className="pill-filter" href={operatorLink(view, { filter: value })} aria-current={value === filter ? "page" : undefined}>{label}</a>)}</nav>}
    </PageHead>
    <DataState status={page.status} onRetry={page.refresh} />
    {page.data && <RecordTable label={config.title} rows={page.data.items} columns={config.columns} idOf={config.id} selected={selected}
      details={row => link({ selected: config.id(row) })} />}
    <p className="foot">{after && <a href={operatorLink(view, { filter })}>First page</a>}{after && page.data?.nextCursor && " · "}
      {page.data?.nextCursor && <a href={link({ after: page.data.nextCursor, selected: null })}>Next page</a>}</p>
    {selected && <><DataState status={detail.status} onRetry={detail.refresh} />
      {detail.data && <InspectionPanel base={view} params={{ filter, after, selected, keysAfter: params.keysAfter ?? null,
        filtersAfter: params.filtersAfter ?? null, submissionsAfter: params.submissionsAfter ?? null }} detail={detail.data} fresh={detail.status === "fresh"}
        onAccessFailure={onAccessFailure} onApplied={() => { page.refresh(); detail.refresh(); refreshCounts(); }} />}</>}
  </section>;
}
