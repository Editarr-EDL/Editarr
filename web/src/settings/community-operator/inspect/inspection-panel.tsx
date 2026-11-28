import type { OperatorInspection, OperatorPage, OperatorUsage } from "../../../api/api-community-operator";
import { ModerationActions } from "../moderation-actions";
import { parseResource } from "../moderation-commands";
import { Facts, observedTime, OperatorTable, units } from "./operator-table";
import { itemLink, operatorLink, resourceLink } from "../routes";

export interface InspectionDetail { inspection: OperatorInspection | null; usage: OperatorUsage | null; keys: OperatorPage | null;
  filters: OperatorPage | null; submissions: OperatorPage | null }
export const emptyDetail: InspectionDetail = { inspection: null, usage: null, keys: null, filters: null, submissions: null };

/** Links from an inspected resource to the resources its state names. */
function relatedLinks({ resource, state }: OperatorInspection) {
  const { kind, targetId } = parseResource(resource), links: [string, string][] = [];
  const id = (key: string) => typeof state[key] === "string" ? state[key] as string : null;
  if (kind === "report") links.push([`Inspect reported ${state.targetType}`, resourceLink(state.targetType === "filter" ? "filter" : "version", id("targetId")!)],
    ["Inspect reporter", resourceLink("contributor", id("contributorId")!)]);
  if (kind === "version" && id("workId")) links.push(["Open item", itemLink(id("workId")!, targetId)]);
  if (id("versionId") && kind !== "version") links.push(["Inspect version", resourceLink("version", id("versionId")!)]);
  if (id("mergedInto") && kind === "version") links.push(["Inspect merge target", resourceLink("version", id("mergedInto")!)]);
  if (id("filterId") && kind !== "filter") links.push(["Inspect filter", resourceLink("filter", id("filterId")!)]);
  if (kind === "filter") links.push(["Open filter on its item", operatorLink(`filters/${targetId}`)]);
  if (id("submissionId") && kind !== "submission") links.push(["Inspect submission", resourceLink("submission", id("submissionId")!)]);
  if (id("contributorId") && ["submission", "vote"].includes(kind)) links.push(["Inspect contributor", resourceLink("contributor", id("contributorId")!)]);
  if (kind === "contributor") links.push(["Look up a vote by this contributor", operatorLink("inspect", { kind: "vote", contributor: targetId })]);
  if (kind === "filter") links.push(["Look up a vote on this filter", operatorLink("inspect", { kind: "vote", selected: targetId })]);
  return links;
}

export function OperatorUsageView({ data }: { data: OperatorUsage }) {
  return <section aria-label="Contributor usage"><h3>Contributor usage</h3><dl>
    <dt>Payload bytes</dt><dd>{units(data.storage.bytes)}</dd><dt>Storage source</dt><dd>{data.storage.source}</dd>
    <dt>Storage observed at</dt><dd>{observedTime(data.storage.observedAt)}</dd></dl>
    <h3>Daily activity</h3><p className="operator-meta">{data.period} UTC</p>
    {data.observations.map(row => <dl key={`${row.resource}:${row.outcome}`} className="operator-activity"><dt>{row.resource}: {row.outcome}</dt>
      <dd>{units(row.occurrences)} requests | {units(row.units)} units</dd>
      <dt>Observed at</dt><dd>{observedTime(row.observedAt)}</dd></dl>)}</section>;
}

/** The inspected resource: state, related links, permitted actions, audit and per-kind detail pages. */
export function InspectionPanel({ params, detail, fresh, onAccessFailure, onApplied, base = "inspect" }: { params: Record<string, string | null>;
  detail: InspectionDetail; fresh: boolean; onAccessFailure: (status: number) => void; onApplied: () => void; base?: string }) {
  const { inspection, usage, keys, filters, submissions } = detail;
  const here = (values: Record<string, string | null>) => operatorLink(base, { ...params, ...values });
  const close = base === "inspect" ? operatorLink("inspect") : operatorLink(base, { ...params, selected: null, kind: null, keysAfter: null,
    filtersAfter: null, submissionsAfter: null });
  return <section aria-label="Resource inspection" className="card inspection">
    <div className="panel-head"><h2>Inspection</h2><a href={close}>Close inspection</a></div>
    <div className="sec">
    {inspection ? <><dl className="facts"><dt>Resource</dt><dd>{inspection.resource}</dd><dt>Optimistic revision</dt><dd>{inspection.revision}</dd></dl>
      <Facts row={inspection.state} />
      <div className="operator-pagination">{relatedLinks(inspection).map(([label, href]) => <a key={label} href={href}>{label}</a>)}</div>
      <ModerationActions key={inspection.resource} inspection={inspection} enabled={fresh}
        onAccessFailure={onAccessFailure} onApplied={onApplied} />
      <h3>Recent audit</h3>{inspection.audit.length === 0 ? <p className="muted">No audit entries</p> :
        <ol className="operator-audit">{inspection.audit.map(row => <li key={String(row.actionId)}><Facts row={row} /></li>)}</ol>}</> :
      fresh ? <p>Selected resource unavailable</p> : null}
    {usage && <OperatorUsageView data={usage} />}
    {keys && <section aria-label="Key history"><h3>Keys</h3>{keys.items.length === 0 ? <p>No keys</p> :
      <ul className="operator-key-list">{keys.items.map(row => <li key={String(row.keyId)}><Facts row={row} /></li>)}</ul>}
      <div className="operator-pagination">{params.keysAfter && <a href={here({ keysAfter: null })}>First key page</a>}
        {keys.nextCursor && <a href={here({ keysAfter: keys.nextCursor })}>Next key page</a>}</div></section>}
    {filters && <section aria-label="Master filters"><h3>Master filters</h3>
      <p className="operator-meta">Attestations are shown as counts only. Inspect an attestation by its submission and entry ID.</p>
      <OperatorTable label="Master filters" page={filters} unit="filter page"
        inspect={row => ({ href: operatorLink("inspect", { kind: "filter", selected: String(row.filterId) }), id: String(row.filterId) })}
        first={params.filtersAfter ? here({ filtersAfter: null }) : null}
        next={filters.nextCursor ? here({ filtersAfter: filters.nextCursor }) : null} /></section>}
    {submissions && <section aria-label="Submissions"><h3>Submissions</h3>
      <OperatorTable label="Submissions" page={submissions} unit="submission page"
        inspect={row => ({ href: resourceLink("submission", String(row.submissionId)), id: String(row.submissionId) })}
        first={params.submissionsAfter ? here({ submissionsAfter: null }) : null}
        next={submissions.nextCursor ? here({ submissionsAfter: submissions.nextCursor }) : null} /></section>}
    </div>
  </section>;
}
