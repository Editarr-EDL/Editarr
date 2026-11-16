import { useEffect, useState } from "react";
import { readOperatorOverview, readOperatorPage, readOperatorInspection, readOperatorUsage,
  type OperatorOverview, type OperatorPage, type OperatorInspection, type OperatorUsage, type OperatorRow } from "../../api/api-community-operator";
import { OperatorAccessError } from "./runtime";
import { OperatorOverviewView, OperatorUsageView, observedTime } from "./overview";
import { ModerationActions, VoteInspection } from "./moderation-actions";

const views = { overview: "Overview", contributions: "Contributions", reports: "Reports", contributors: "Contributors",
  operations: "Operations", audit: "Audit history", backup: "Backup status" };
type View = keyof typeof views;
export function operatorRoute() {
  const url = new URL(window.location.hash.slice(1) || "/overview", window.location.origin);
  const candidate = url.pathname.slice(1);
  const view: View = Object.prototype.hasOwnProperty.call(views, candidate) ? candidate as View : "overview";
  const selected = url.searchParams.get("selected");
  return { view, after: url.searchParams.get("after"), keysAfter: url.searchParams.get("keysAfter"),
    selected: selected && /^[A-Za-z0-9_.=-]{1,1024}$/.test(selected) ? selected : null };
}
export function operatorLink(view: View, after: string | null = null, selected: string | null = null, keysAfter: string | null = null) {
  const query = new URLSearchParams();
  if (after) query.set("after", after);
  if (selected) query.set("selected", selected);
  if (keysAfter) query.set("keysAfter", keysAfter);
  return `#/${view}${query.size ? `?${query}` : ""}`;
}
function rowId(row: OperatorRow) { return String(row.keyId ?? row.revisionId ?? row.reportId ?? row.contributorId ?? row.operationId ?? row.actionId); }
function listId(view: View, row: OperatorRow) {
  if (view === "operations") return btoa(JSON.stringify([row.contributorId, row.kind, row.operationId])).replace(/\+/g, "-").replace(/\//g, "_");
  return String(view === "contributions" ? row.revisionId : view === "reports" ? row.reportId :
    view === "audit" ? row.actionId : row.contributorId);
}
const captions: Record<string, string> = { revisionId: "Revision", contributorId: "Contributor", contributionId: "Contribution",
  displayName: "Public name", authorState: "Author state", createdAt: "Created", totalEvents: "Events", payloadBytes: "Payload bytes",
  qualityScore: "Revision quality", operationId: "Operation", updatedAt: "Updated", reportId: "Report", note: "Private note",
  expectedRevision: "Expected revision", actionId: "Action", installationId: "Installation", keyId: "Key", retiredAt: "Retired" };
function display(key: string, value: string | number | null) {
  if (value === null) return "Unknown";
  if (["createdAt", "updatedAt", "retiredAt"].includes(key) && typeof value === "number") return observedTime(value);
  return String(value);
}
function Facts({ row }: { row: OperatorRow }) {
  return <dl>{Object.entries(row).map(([key, value]) => <div className="operator-fact" key={key}>
    <dt>{captions[key] ?? key}</dt><dd>{display(key, value)}</dd></div>)}</dl>;
}

export function OperatorDashboard({ onAccessFailure }: { onAccessFailure: (status: number) => void }) {
  const [route, setRoute] = useState(operatorRoute);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<OperatorOverview | OperatorPage | null>(null);
  const [inspection, setInspection] = useState<OperatorInspection | null>(null);
  const [usage, setUsage] = useState<OperatorUsage | null>(null);
  const [keys, setKeys] = useState<OperatorPage | null>(null);
  const [status, setStatus] = useState<"loading" | "fresh" | "stale" | "missing" | "unavailable">("loading");
  useEffect(() => {
    const changed = () => { setData(null); setInspection(null); setUsage(null); setKeys(null); setRoute(operatorRoute()); };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    const load = async () => {
      const value = route.view === "overview" || route.view === "backup" ? await readOperatorOverview(controller.signal) :
        await readOperatorPage(route.view, route.after, controller.signal);
      let detail: OperatorInspection | null = null, detailUsage: OperatorUsage | null = null, detailKeys: OperatorPage | null = null;
      if (route.selected && ["contributions", "contributors", "reports"].includes(route.view)) {
        detail = await readOperatorInspection(route.view === "contributions" ? "revision" : route.view === "contributors" ? "contributor" : "report", route.selected, controller.signal);
        if (route.view === "contributors") {
          detailUsage = await readOperatorUsage(route.selected, controller.signal);
          detailKeys = await readOperatorPage("keys", route.keysAfter, controller.signal, route.selected);
        }
      }
      if (!controller.signal.aborted) { setData(value); setInspection(detail); setUsage(detailUsage); setKeys(detailKeys); setStatus("fresh"); }
    };
    void load().catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) {
        setData(null); setInspection(null); setUsage(null); setKeys(null); onAccessFailure(error.status); return;
      }
      if (error instanceof OperatorAccessError && error.status === 404) {
        setData(null); setInspection(null); setUsage(null); setKeys(null); setStatus("missing"); return;
      }
      setStatus(data ? "stale" : "unavailable");
    });
    return () => controller.abort();
  }, [route, attempt]);
  const page = data && "items" in data ? data : null;
  const overview = data && "storage" in data ? data : null;
  const selectedRow = page?.items.find(row => listId(route.view, row) === route.selected);
  return <>
    <nav aria-label="Operator pages" className="operator-nav">{Object.entries(views).map(([view, label]) =>
      <a key={view} href={operatorLink(view as View)} aria-current={view === route.view ? "page" : undefined}>{label}</a>)}</nav>
    <div className="operator-heading"><h2>{views[route.view]}</h2>
      <button disabled={status === "loading"} onClick={() => setAttempt(value => value + 1)}>Refresh data</button></div>
    <p role={status === "unavailable" || status === "stale" || status === "missing" ? "alert" : "status"} className="operator-data-status">
      {{ loading: "Loading service data...", fresh: "Service snapshot loaded", stale: "Stale snapshot. Refresh unavailable.",
        missing: "Selected resource not found", unavailable: "Service data unavailable" }[status]}</p>
    <div aria-busy={status === "loading"} className={status === "stale" || status === "loading" ? "operator-snapshot-stale" : undefined}>
      {overview && <OperatorOverviewView data={overview} backupOnly={route.view === "backup"} />}
      {page && <section aria-label={`${views[route.view]} list`}><p className="operator-meta">{page.source} | Snapshot: {observedTime(page.observedAt)}</p>
        {page.items.length === 0 ? <p>No records</p> : <div className="operator-table" role="region" aria-label={`${views[route.view]} table`} tabIndex={0}>
          <table><thead><tr><th>Inspect</th>{Object.keys(page.items[0]).map(key => <th key={key}>{captions[key] ?? key}</th>)}</tr></thead>
            <tbody>{page.items.map(row => <tr key={JSON.stringify(row)}><td><a href={operatorLink(route.view, route.after, listId(route.view, row))}>Inspect <span className="operator-sr-only">{listId(route.view, row)}</span></a></td>
              {Object.entries(row).map(([key, value]) => <td key={key}>{display(key, value)}</td>)}</tr>)}</tbody></table></div>}
        <div className="operator-pagination">{route.after && <a href={operatorLink(route.view)}>First page</a>}
          {page.nextCursor && <a href={operatorLink(route.view, page.nextCursor)}>Next page</a>}</div></section>}
      {route.selected && <section aria-label="Resource inspection" className="operator-inspection">
        <div className="operator-heading"><h3>Inspection</h3><a href={operatorLink(route.view, route.after)}>Close inspection</a></div>
        {inspection ? <><dl><dt>Resource</dt><dd>{inspection.resource}</dd><dt>Optimistic revision</dt><dd>{inspection.revision}</dd></dl>
          <Facts row={inspection.state} />
          {route.view === "reports" && <div className="operator-pagination">
            <a href={operatorLink("contributions", null, String(inspection.state.revisionId))}>Inspect reported revision</a>
            <a href={operatorLink("contributors", null, String(inspection.state.contributorId))}>Inspect reporter</a></div>}
          <ModerationActions key={inspection.resource} inspection={inspection} enabled={status === "fresh"}
            onAccessFailure={onAccessFailure} onApplied={() => setAttempt(value => value + 1)} />
          <h3>Recent audit</h3>{inspection.audit.length === 0 ? <p>No audit entries</p> :
            <ol className="operator-audit">{inspection.audit.map(row => <li key={String(row.actionId)}><Facts row={row} /></li>)}</ol>}</> :
          selectedRow ? <Facts row={selectedRow} /> : status === "fresh" ? <p>Selected resource unavailable in this page</p> : null}
        {usage && <OperatorUsageView data={usage} />}
        {inspection && route.view === "contributors" && status === "fresh" && <VoteInspection key={inspection.resource}
          contributorId={String(inspection.state.contributorId)} onAccessFailure={onAccessFailure} />}
        {keys && <section aria-label="Key history"><h3>Keys</h3>{keys.items.length === 0 ? <p>No keys</p> :
          <ul className="operator-key-list">{keys.items.map(row => <li key={rowId(row)}><Facts row={row} /></li>)}</ul>}
          <div className="operator-pagination">{route.keysAfter && <a href={operatorLink(route.view, route.after, route.selected)}>First key page</a>}
            {keys.nextCursor && <a href={operatorLink(route.view, route.after, route.selected, keys.nextCursor)}>Next key page</a>}</div></section>}
      </section>}
    </div>
  </>;
}