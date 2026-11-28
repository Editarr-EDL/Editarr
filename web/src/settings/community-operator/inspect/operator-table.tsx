import type { OperatorPage, OperatorRow } from "../../../api/api-community-operator";

export const observedTime = (value: number | null) => value === null ? "Unknown" : new Date(value).toISOString();
export const units = (value: number | null) => value === null ? "Unknown" : value.toLocaleString("en-US");

const captions: Record<string, string> = { contributorId: "Contributor", displayName: "Public name", createdAt: "Created",
  operationId: "Operation", updatedAt: "Updated", reportId: "Report", note: "Private note", expectedRevision: "Expected revision",
  actionId: "Action", installationId: "Installation", keyId: "Key", retiredAt: "Retired", errorCode: "Error",
  versionId: "Version", workId: "Work", aliasId: "Alias", labelText: "Label", mergedInto: "Merged into", runtimeMs: "Runtime (ms)",
  trust: "Trust", revision: "Master revision", quarantined: "Quarantined", contributorCount: "Contributors",
  filterCount: "Filters", servedFilterCount: "Served filters", referenceSubmissionId: "Reference submission",
  suggestionId: "Suggestion", normalized: "Normalized", text: "Text", submissionId: "Submission",
  targetType: "Target type", targetId: "Target", filterId: "Filter", entryId: "Entry", startMs: "Start (ms)", endMs: "End (ms)",
  ignoredReason: "Ignored reason", attestations: "Attestations", upvotes: "Upvotes", downvotes: "Downvotes", disputes: "Disputes",
  served: "Served", excluded: "Excluded", status: "Status", category: "Category", subcategory: "Subcategory", severity: "Severity",
  action: "Filter action", confidence: "Confidence", tier: "Evidence tier" };
export const caption = (key: string) => captions[key] ?? key;
const flags = new Set(["quarantined", "served", "excluded"]);
export function display(key: string, value: string | number | null) {
  if (value === null) return "Unknown";
  if (["createdAt", "updatedAt", "retiredAt"].includes(key) && typeof value === "number") return observedTime(value);
  if (flags.has(key) && (value === 0 || value === 1)) return value ? "Yes" : "No";
  return String(value);
}
export function Facts({ row }: { row: OperatorRow }) {
  return <dl>{Object.entries(row).map(([key, value]) => <div className="operator-fact" key={key}>
    <dt>{caption(key)}</dt><dd>{display(key, value)}</dd></div>)}</dl>;
}

/** A bounded list page with one inspect link per row and first/next page links. */
export function OperatorTable({ label, page, inspect, first, next, unit = "page" }: { label: string; page: OperatorPage;
  inspect: (row: OperatorRow) => { href: string; id: string }; first: string | null; next: string | null; unit?: string }) {
  return <section aria-label={`${label} list`}><p className="operator-meta">{page.source} | Snapshot: {observedTime(page.observedAt)}</p>
    {page.items.length === 0 ? <p>No records</p> : <div className="tw" role="region" aria-label={`${label} table`} tabIndex={0}>
      <table className="t"><thead><tr><th>Inspect</th>{Object.keys(page.items[0]).map(key => <th key={key}>{caption(key)}</th>)}</tr></thead>
        <tbody>{page.items.map(row => {
          const link = inspect(row);
          return <tr key={JSON.stringify(row)}><td><a href={link.href}>Inspect <span className="operator-sr-only">{link.id}</span></a></td>
            {Object.entries(row).map(([key, value]) => <td key={key}>{display(key, value)}</td>)}</tr>;
        })}</tbody></table></div>}
    <div className="operator-pagination">{first && <a href={first}>First {unit}</a>}
      {next && <a href={next}>Next {unit}</a>}</div></section>;
}
