import type { OperatorOverview, OperatorUsage } from "../../api/api-community-operator";

export function observedTime(value: number | null) {
  return value === null ? "Unknown" : new Date(value).toISOString();
}
export function units(value: number | null) { return value === null ? "Unknown" : value.toLocaleString("en-US"); }

export function OperatorOverviewView({ data, backupOnly = false }: { data: OperatorOverview; backupOnly?: boolean }) {
  if (backupOnly) return <section aria-label="Backup status"><h2>Backup and recovery</h2>
    <dl><dt>Last backup</dt><dd>Unavailable</dd><dt>Isolated restore</dt><dd>Unavailable</dd>
      <dt>Source</dt><dd>{data.backup.source}</dd><dt>Observed at</dt><dd>{observedTime(data.backup.observedAt)}</dd></dl></section>;
  return <>
    <section aria-label="Service state"><h2>Service state</h2><dl>
      <dt>Snapshot</dt><dd>{observedTime(data.observedAt)}</dd></dl></section>
    <section aria-label="Payload storage"><h2>Artifact payload storage</h2><dl>
      <dt>Global payload bytes</dt><dd>{units(data.storage.globalBytes)}</dd>
      <dt>Source</dt><dd>{data.storage.source} (not provider verified)</dd>
      <dt>Observed at</dt><dd>{observedTime(data.storage.observedAt)}</dd></dl></section>
    <section aria-label="Provider measurements"><h2>Provider measurements</h2><dl>
      <dt>Total SQLite database bytes</dt><dd>Unavailable</dd>
      <dt>Source</dt><dd>{data.databaseSize.source}</dd><dt>Observed at</dt><dd>{observedTime(data.databaseSize.observedAt)}</dd></dl></section>
    <section aria-label="Daily application activity"><h2>Daily application activity</h2><p className="operator-meta">
      {data.period} UTC | {data.activity.source} | Restore provenance: {data.activity.restoreProvenance}</p>
      <div className="operator-table" role="region" aria-label="Daily application activity table" tabIndex={0}><table><thead><tr>
        <th>Resource</th><th>Observed units</th><th>Observed at</th></tr></thead>
        <tbody>{Object.entries(data.activity.resources).map(([resource, value]) => <tr key={resource}>
          <th scope="row">{resource}</th><td>{units(value.observed)}</td>
          <td>{observedTime(value.observedAt)}</td></tr>)}</tbody></table></div></section>
    <section aria-label="Handler observations"><h2>Daily handler observations</h2><p className="operator-meta">
      {data.period} UTC | Partial, overlapping application evidence | Not provider verified</p>
      <div className="operator-table" role="region" aria-label="Handler observations table" tabIndex={0}><table><thead><tr>
        <th>Handler</th><th>Occurrences</th><th>Known reads</th><th>Unknown reads</th><th>Known writes</th><th>Unknown writes</th><th>Source</th><th>Observed at</th></tr></thead>
        <tbody>{data.observations.map(row => <tr key={row.handler}><th scope="row">{row.handler}</th>
          <td>{units(row.occurrences)}</td><td>{units(row.reads)}</td><td>{units(row.unknownReads)}</td>
          <td>{units(row.writes)}</td><td>{units(row.unknownWrites)}</td><td>{row.source}</td><td>{observedTime(row.observedAt)}</td></tr>)}</tbody></table></div></section>
  </>;
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