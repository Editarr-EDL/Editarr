import { readOperatorOverview, readOperatorPage } from "../../../api/api-community-operator";
import { operationColumns, RecordTable } from "../records/record-tables";
import { count, dayTime, titleCase } from "../shared/format";
import { DataState, useOperatorData } from "../shared/operator-data";
import { operatorLink } from "../routes";

const units = (value: number | null) => value === null ? "Unknown" : count(value);
const when = (value: number | null) => value === null ? "Not observed" : dayTime(value);

/**
 * Today's application-observed activity and per-handler observations (partial, overlapping, not provider-verified),
 * and the paged contributor operations log.
 */
export function ServiceRecords({ params }: { params: Record<string, string | null> }) {
  const overview = useOperatorData("overview", readOperatorOverview);
  const opsAfter = params.opsAfter ?? null;
  const operations = useOperatorData(`operations:${opsAfter}`, signal => readOperatorPage("operations", opsAfter, signal));
  const data = overview.data;
  return <>
    <div className="section-title"><h2 id="handler-title">Handler observations</h2>
      <span className="muted small">{data ? `${data.period} UTC · ` : ""}Partial, overlapping application evidence · not provider-verified</span></div>
    <DataState status={overview.status} onRetry={overview.refresh} />
    {data && <div className="tw" role="region" aria-labelledby="handler-title" tabIndex={0}><table className="t">
      <caption className="sr">Daily handler observations</caption>
      <thead><tr><th scope="col">Handler</th><th scope="col" className="r">Occurrences</th><th scope="col" className="r">Known reads</th>
        <th scope="col" className="r">Unknown reads</th><th scope="col" className="r">Known writes</th><th scope="col" className="r">Unknown writes</th>
        <th scope="col">Observed</th></tr></thead>
      <tbody>{data.observations.map(row => <tr key={row.handler}><th scope="row">{titleCase(row.handler)}</th>
        <td className="r num">{units(row.occurrences)}</td><td className="r num">{units(row.reads)}</td><td className="r num">{units(row.unknownReads)}</td>
        <td className="r num">{units(row.writes)}</td><td className="r num">{units(row.unknownWrites)}</td><td>{when(row.observedAt)}</td></tr>)}
        {Object.entries(data.activity.resources).map(([resource, value]) => <tr key={resource} className="subtotal"><th scope="row">
          {resource === "worker_requests" ? "All requests" : "All database writes"}</th>
          <td className="r num">{units(value.observed)}</td><td colSpan={4} className="muted">Application total · restore provenance {data.activity.restoreProvenance}</td>
          <td>{when(value.observedAt)}</td></tr>)}</tbody></table></div>}
    {data && <p className="foot">Payload storage counted by the service: {count(data.storage.globalBytes)} bytes · database size, provider usage and
      provider-side backup status are not available to the service.</p>}
    <div className="section-title"><h2>Operations log</h2><span className="muted small">Contributor publication, import and key operations, newest first</span></div>
    <DataState status={operations.status} onRetry={operations.refresh} />
    {operations.data && <RecordTable label="Operations log" rows={operations.data.items} columns={operationColumns}
      idOf={row => `${row.contributorId}:${row.kind}:${row.operationId}`} />}
    <p className="foot">{opsAfter && <a href={operatorLink("health", { ...params, opsAfter: null })}>First operations page</a>}
      {opsAfter && operations.data?.nextCursor && " · "}
      {operations.data?.nextCursor && <a href={operatorLink("health", { ...params, opsAfter: operations.data.nextCursor })}>Next operations page</a>}</p>
  </>;
}
