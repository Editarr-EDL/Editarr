import { useEffect, useState } from "react";
import { readOperatorOverview, readOperatorPage, readOperatorInspection, readOperatorUsage, operatorResources,
  type OperatorList, type OperatorOverview, type OperatorPage, type OperatorResource, type OperatorRow } from "../../api/api-community-operator";
import { OperatorAccessError } from "./runtime";
import { OperatorOverviewView } from "./overview";
import { OperatorTable } from "./operator-table";
import { emptyDetail, InspectionPanel, type InspectionDetail } from "./inspection-panel";
import { ResourceLookup } from "./resource-lookup";
import { RecomputeAll } from "./recompute-all";
import { activeFilter, listFilters, operatorLink, operatorRoute, operatorViews, type OperatorRoute, type OperatorView } from "./routes";

const listViews: Partial<Record<OperatorView, OperatorList>> = { versions: "versions", aliases: "aliases", reports: "reports",
  contributors: "contributors", operations: "operations", audit: "audit" };
const inspectedKinds: Partial<Record<OperatorView, OperatorResource>> = { versions: "version", aliases: "alias_suggestion",
  reports: "report", contributors: "contributor" };
function listId(view: OperatorView, row: OperatorRow) {
  if (view === "operations") return btoa(JSON.stringify([row.contributorId, row.kind, row.operationId])).replace(/\+/g, "-").replace(/\//g, "_");
  return String({ versions: row.versionId, aliases: row.suggestionId, reports: row.reportId, audit: row.actionId }[view as string] ?? row.contributorId);
}
function inspectedKind(route: OperatorRoute) {
  if (route.view === "inspect") return operatorResources.includes(route.kind as OperatorResource) ? route.kind as OperatorResource : null;
  return inspectedKinds[route.view] ?? null;
}

/** Load the route's list page and, when a resource is selected, its inspection and per-kind detail pages. */
async function loadRoute(route: OperatorRoute, signal: AbortSignal) {
  const list = listViews[route.view];
  const data = route.view === "overview" || route.view === "backup" ? await readOperatorOverview(signal) :
    list ? await readOperatorPage(list, route.after, signal, undefined, activeFilter(route)) : null;
  const detail: InspectionDetail = { ...emptyDetail }, kind = inspectedKind(route), id = route.selected;
  if (id && kind) {
    detail.inspection = await readOperatorInspection(kind, id, signal, kind === "vote" ? route.contributor ?? "" : undefined);
    if (kind === "contributor") {
      detail.usage = await readOperatorUsage(id, signal);
      detail.keys = await readOperatorPage("keys", route.keysAfter, signal, id);
    }
    if (kind === "version") {
      detail.filters = await readOperatorPage("filters", route.filtersAfter, signal, id);
      detail.submissions = await readOperatorPage("submissions", route.submissionsAfter, signal, id);
    }
  }
  return { data, detail };
}

export function OperatorDashboard({ onAccessFailure }: { onAccessFailure: (status: number) => void }) {
  const [route, setRoute] = useState(operatorRoute);
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<OperatorOverview | OperatorPage | null>(null);
  const [detail, setDetail] = useState<InspectionDetail>(emptyDetail);
  const [status, setStatus] = useState<"loading" | "fresh" | "stale" | "missing" | "unavailable">("loading");
  useEffect(() => {
    const changed = () => { setData(null); setDetail(emptyDetail); setRoute(operatorRoute()); };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStatus("loading");
    void loadRoute(route, controller.signal).then(value => {
      if (!controller.signal.aborted) { setData(value.data); setDetail(value.detail); setStatus("fresh"); }
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      if (error instanceof OperatorAccessError && [401, 403].includes(error.status)) {
        setData(null); setDetail(emptyDetail); onAccessFailure(error.status); return;
      }
      if (error instanceof OperatorAccessError && [400, 404].includes(error.status) && route.selected) {
        setData(null); setDetail(emptyDetail); setStatus("missing"); return;
      }
      setStatus(data ? "stale" : "unavailable");
    });
    return () => controller.abort();
  }, [route, attempt]);
  const page = data && "items" in data ? data : null;
  const overview = data && "storage" in data ? data : null;
  const selectedRow = page?.items.find(row => listId(route.view, row) === route.selected);
  const choices = listFilters[route.view], filter = route.filter ?? choices?.[0];
  const label = operatorViews[route.view];
  return <>
    <nav aria-label="Operator pages" className="operator-nav">{Object.entries(operatorViews).map(([view, text]) =>
      <a key={view} href={operatorLink(view as OperatorView)} aria-current={view === route.view ? "page" : undefined}>{text}</a>)}</nav>
    <div className="operator-heading"><h2>{label}</h2>
      <button disabled={status === "loading"} onClick={() => setAttempt(value => value + 1)}>Refresh data</button></div>
    <p role={status === "unavailable" || status === "stale" || status === "missing" ? "alert" : "status"} className="operator-data-status">
      {{ loading: "Loading service data...", fresh: "Service snapshot loaded", stale: "Stale snapshot. Refresh unavailable.",
        missing: "Selected resource not found", unavailable: "Service data unavailable" }[status]}</p>
    <div aria-busy={status === "loading"} className={status === "stale" || status === "loading" ? "operator-snapshot-stale" : undefined}>
      {overview && <OperatorOverviewView data={overview} backupOnly={route.view === "backup"} />}
      {choices && <nav aria-label={`${label} filter`} className="operator-filter">{choices.map(choice =>
        <a key={choice} href={operatorLink(route.view, { filter: choice })} aria-current={choice === filter ? "true" : undefined}>
          {choice === "all" ? "All" : choice.replace("_", " ")}</a>)}</nav>}
      {page && <OperatorTable label={label} page={page}
        inspect={row => ({ href: operatorLink(route.view, { after: route.after, filter: route.filter, selected: listId(route.view, row) }),
          id: listId(route.view, row) })}
        first={route.after ? operatorLink(route.view, { filter: route.filter }) : null}
        next={page.nextCursor ? operatorLink(route.view, { after: page.nextCursor, filter: route.filter }) : null} />}
      {route.view === "inspect" && <ResourceLookup key={`${route.kind}:${route.selected}:${route.contributor}`} route={route} />}
      {route.view === "recompute" && <RecomputeAll onAccessFailure={onAccessFailure} />}
      {route.selected && (route.view !== "inspect" || inspectedKind(route)) && <InspectionPanel route={route} detail={detail}
        selectedRow={selectedRow} fresh={status === "fresh"} onAccessFailure={onAccessFailure} onApplied={() => setAttempt(value => value + 1)} />}
    </div>
  </>;
}
