import { useEffect, useState } from "react";
import { itemSorts, readOperatorItems, type ItemRow, type ItemSort, type ItemVersion } from "../../../api/api-community-operator-catalog";
import { readOperatorHome } from "../../../api/api-community-operator-community";
import { ago, count, delta, itemName, plural, versionName } from "../shared/format";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { AvatarStack, PageHead, Poster, Segmented } from "../shared/ui";
import { itemLink, navigate, operatorLink } from "../routes";

const sorts = [["updated", "Recently updated"], ["uploads", "Most uploads"], ["votes", "Most votes"], ["reports", "Most reports"], ["title", "Title"]] as const;

export function VersionChip({ version }: { version: ItemVersion }) {
  const tone = version.quarantined ? " bad" : version.status === "candidate" ? " cand" : version.status === "merged_into" ? " dim" : "";
  return <span className={`chip${tone}`} title={version.quarantined ? "Quarantined" : undefined}>{versionName(version)}
    {version.status === "candidate" ? " · candidate" : version.status === "merged_into" ? " · merged" : ""}
    {version.quarantined ? <span className="sr"> (quarantined)</span> : null}</span>;
}
const latest = (row: ItemRow) => Math.max(0, ...row.versions.map(version => version.revision));
const subtitle = (row: ItemRow) => `${row.year ?? "Year unknown"} · ${row.kind === "episode" ? "Episode" : "Movie"}`;
function NetVotes({ value }: { value: number }) {
  return <b className={`num ${value < 0 ? "down" : "up"}`}>{value >= 0 ? "+" : "−"}{count(Math.abs(value))}</b>;
}
function Reports({ value }: { value: number }) {
  return value ? <span className="chip bad">{value} open</span> : <span className="rep0"><span aria-hidden="true">—</span><span className="sr">None</span></span>;
}

/** Every work with a community master set; sortable, filterable and paged, as a table, grid or mobile list. */
export function ItemsPage({ params }: { params: Record<string, string | null> }) {
  const { counts } = useOperator();
  const sort = (itemSorts.includes(params.sort as ItemSort) ? params.sort : "updated") as ItemSort;
  const q = params.q ?? "", after = params.after ?? null;
  const [draft, setDraft] = useState(q);
  const [grid, setGrid] = useState(false);
  const { data, status, refresh } = useOperatorData(`items:${sort}:${q}:${after}`, signal => readOperatorItems(sort, q, after, signal));
  const week = useOperatorData("home:7d", signal => readOperatorHome("7d", signal));
  useEffect(() => setDraft(q), [q]);
  useEffect(() => {
    if (draft.trim() === q.trim()) return;
    const timer = window.setTimeout(() => window.location.replace(operatorLink("items", { sort, q: draft.trim() })), 300);
    return () => window.clearTimeout(timer);
  }, [draft]);
  const go = (value: ItemSort) => navigate(operatorLink("items", { sort: value, q }));
  const rows = data?.items ?? [], kpis = week.data?.kpis;
  const versions = rows.reduce((sum, row) => sum + row.versions.length, 0), filters = rows.reduce((sum, row) => sum + row.filters, 0);
  const change = (value: number, previous: number | null) => delta(value, previous)?.text.replace("▲ ", "+").replace("▼ ", "−");
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="Community catalog" title="Items" intro="Every work with a community master set, newest changes first." />
    {counts && counts.all > 0 && <div className="attn" role="region" aria-label="Needs attention"><strong>Needs attention</strong>
      <a className="q" href="#/queue/reports"><b>{counts.reports}</b> open reports</a>
      <a className="q" href="#/queue/aliases"><b>{counts.aliases}</b> alias suggestions</a>
      <a className="q" href="#/queue/versions"><b>{counts.versions}</b> candidate versions</a>
      <a className="q" href="#/queue/quarantined"><b>{counts.quarantined}</b> quarantined</a>
      <a className="btn primary" href="#/queue">Open moderation →</a></div>}
    <div className="stats">
      <div className="stat"><b>{data ? `${rows.length}${data.nextCursor ? "+" : ""}` : "—"}</b><span>Items · {plural(versions, "version")}</span></div>
      <div className="stat"><b>{data ? count(filters) : "—"}</b><span>Master filters{data?.nextCursor ? " on this page" : ""}</span></div>
      <div className="stat"><b>{kpis ? count(kpis.uploads.value) : "—"}</b><span>Uploads this week</span>
        {kpis && <em>{change(kpis.uploads.value, kpis.uploads.previous)}</em>}</div>
      <div className="stat"><b>{kpis ? count(kpis.votes.total.value) : "—"}</b><span>Votes this week</span>
        {kpis && <em>{change(kpis.votes.total.value, kpis.votes.total.previous)}</em>}</div>
      <div className="stat"><b>{kpis ? count(kpis.activeContributors.value) : "—"}</b><span>Active contributors (7d)</span></div>
    </div>
    <div className="toolbar">
      <div className="search"><label htmlFor="items-filter" className="sr">Filter items by title or year</label>
        <input id="items-filter" type="search" placeholder="Filter by title or year" value={draft} maxLength={200} onChange={event => setDraft(event.target.value)} /></div>
      <div className="right"><span className="lbl sort-l" aria-hidden="true">Sort</span>
        <label htmlFor="items-sort" className="sr">Sort items</label>
        <select id="items-sort" className="btn sort-sel" value={sort} onChange={event => go(event.target.value as ItemSort)}>
          {sorts.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        <Segmented label="Sort items" value={sort} options={sorts} onChange={go} className="seg sort-seg" />
        <Segmented label="View" value={grid ? "grid" : "list"} className="viewtog" onChange={value => setGrid(value === "grid")}
          options={[["list", <><span aria-hidden="true">☰</span><span className="sr">List</span></>],
            ["grid", <><span aria-hidden="true">▦</span><span className="sr">Grid</span></>]]} />
      </div>
    </div>
    <DataState status={status} onRetry={refresh} />
    {data && !rows.length && <div className="card empty"><p>{q ? `No items match “${q}”.` : "No community master sets yet."}</p></div>}
    {rows.length > 0 && (grid ? <div className="grid">{rows.map(row => <a key={row.workId} className="gcard" href={itemLink(row.workId)}>
      <Poster item={row} className="wide" /><div><b>{itemName(row)}</b><div className="muted small">{subtitle(row)} · {plural(row.versions.length, "version")}</div></div>
      <div className="gmeta"><span>rev {latest(row)} · {ago(row.lastChangeAt)}</span><span>{plural(row.filters, "filter")}</span></div>
      <div className="gmeta"><NetVotes value={row.netVotes} /><Reports value={row.openReports} /></div></a>)}</div> : <>
      <div className="tw items-table" role="region" aria-label="Items table" tabIndex={0}><table className="t"><caption className="sr">Items, sorted by {sorts.find(([value]) => value === sort)![1]}</caption>
        <thead><tr><th scope="col">Item</th><th scope="col">Versions</th><th scope="col">Master</th><th scope="col" className="r">Filters</th>
          <th scope="col">Net votes</th><th scope="col" className="r">Reports</th><th scope="col">Contributors</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.workId}>
          <td><div className="ititle"><Poster item={row} episode={row.kind === "episode"} /><div><b><a href={itemLink(row.workId)}>{itemName(row)}</a></b>
            <span>{subtitle(row)}</span></div></div></td>
          <td><div className="vers">{row.versions.map(version => <VersionChip key={version.versionId} version={version} />)}</div></td>
          <td className="rev"><b>rev {latest(row)}</b><span>{ago(row.lastChangeAt)}</span></td>
          <td className="r num">{count(row.filters)}<div className="faint small">{plural(row.uploads, "upload")}</div></td>
          <td><NetVotes value={row.netVotes} /></td><td className="r"><Reports value={row.openReports} /></td>
          <td><AvatarStack people={row.contributors} /></td></tr>)}</tbody></table></div>
      <ul className="mlist">{rows.map(row => <li key={row.workId}><a className="mrow" href={itemLink(row.workId)}>
        <Poster item={row} episode={row.kind === "episode"} /><div className="grow"><div className="l1"><b>{itemName(row)}</b>
          {row.openReports > 0 && <span className="chip bad">{plural(row.openReports, "report")}</span>}</div>
          <div className="l2">{subtitle(row)} · rev {latest(row)} · {ago(row.lastChangeAt)}</div>
          <div className="vers">{row.versions.map(version => <VersionChip key={version.versionId} version={version} />)}</div>
          <div className="l3"><span>{plural(row.filters, "filter")}</span><span>{plural(row.uploads, "upload")}</span>
            <NetVotes value={row.netVotes} /><AvatarStack people={row.contributors} max={3} /></div></div></a></li>)}</ul></>)}
    <p className="foot">Showing {plural(rows.length, "item")}{after && <> · <a href={operatorLink("items", { sort, q })}>First page</a></>}
      {data?.nextCursor && <> · <a href={operatorLink("items", { sort, q, after: data.nextCursor })}>Next page</a></>}
      {" "}· Upload and vote counts are application-observed totals.</p>
  </section>;
}
