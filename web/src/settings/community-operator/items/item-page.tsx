import { useEffect } from "react";
import { readOperatorFilterContext, readOperatorItem, readOperatorVersionDetail } from "../../../api/api-community-operator-catalog";
import { ago, count, itemName, itemTitle, plural, runtime, titleCase, versionName } from "../shared/format";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { Poster } from "../shared/ui";
import { itemLink, navigate } from "../routes";
import { FilterCard } from "./filter-card";
import { FilterTable, RevisionHistory } from "./filter-table";
import { RuntimeTimeline } from "./runtime-timeline";
import { VersionActions } from "./version-actions";

/** One work: versions, the selected version's runtime timeline, filter table and revisions, and the selected filter's card. */
export function ItemPage({ workId, params }: { workId: string; params: Record<string, string | null> }) {
  const { refreshCounts } = useOperator();
  const item = useOperatorData(`item:${workId}`, signal => readOperatorItem(workId, signal));
  const versions = item.data?.versions ?? [];
  const confirmed = versions.filter(version => version.status === "confirmed").sort((a, b) => b.uploads - a.uploads || b.revision - a.revision);
  const selected = versions.find(version => version.versionId === params.version) ?? confirmed[0] ?? versions[0] ?? null;
  const versionId = selected?.versionId ?? null, filterId = params.filter ?? null;
  const detail = useOperatorData(`version:${workId}:${versionId}`, signal => readOperatorVersionDetail(workId, versionId!, signal), !!versionId);
  const context = useOperatorData(`filter:${filterId}`, signal => readOperatorFilterContext(filterId!, signal), !!filterId);
  useEffect(() => { if (item.data) document.getElementById("page-title")?.focus({ preventScroll: true }); }, [!!item.data]);
  const refresh = () => { item.refresh(); detail.refresh(); context.refresh(); refreshCounts(); };
  const filterHref = (id: string) => itemLink(workId, versionId, id);
  const work = item.data?.work;
  const contributors = new Set(versions.flatMap(version => version.contributors.map(person => person.contributorId))).size;
  const lastChange = Math.max(0, ...versions.map(version => version.revisedAt ?? 0)) || null;
  return <section aria-labelledby="page-title">
    <nav className="crumbs" aria-label="Breadcrumb"><a href="#/items">Items</a> › <span aria-current="page">{work ? itemTitle(work) : workId}</span></nav>
    <DataState status={item.status} onRetry={item.refresh} />
    {work && <>
      <div className="hero">
        <Poster item={work} className="hero-poster" />
        <div><p className="eyebrow">{work.kind === "episode" ? "Episode" : "Movie"}</p>
          <h1 id="page-title" tabIndex={-1}>{itemName(work)} {work.year && <span>({work.year})</span>}</h1>
          <div className="meta">{work.aliases.length > 0 && <span>Also seen as <b>{work.aliases.slice(0, 3).map(alias => `“${alias.title}”`).join(", ")}</b></span>}
            <span><b>{versions.length}</b> {versions.length === 1 ? "version" : "versions"}</span><span><b>{count(contributors)}</b> {contributors === 1 ? "contributor" : "contributors"}</span>
            {work.identifiers.map(identifier => <span key={`${identifier.provider}:${identifier.value}`}>{identifier.provider.toUpperCase()}{" "}
              <b>{identifier.value}{identifier.season !== null ? ` S${identifier.season}E${identifier.episode ?? "?"}` : ""}</b></span>)}
            {lastChange && <span>Last change <b>{ago(lastChange)}</b></span>}</div>
          <div className="vseg" role="group" aria-label="Version">{versions.map(version => <button type="button" key={version.versionId}
            aria-pressed={version.versionId === versionId} className={version.status === "candidate" ? "candidate" : undefined}
            onClick={() => navigate(itemLink(workId, version.versionId))}>
            <b>{versionName(version)}{version.status !== "confirmed" && <span className={`chip mini ${version.status === "candidate" ? "cand" : "dim"}`}>
              {version.status === "candidate" ? "candidate" : "merged"}</span>}{!!version.quarantined && <span className="chip mini bad">quarantined</span>}</b>
            <span>{runtime(version.runtimeMs)} · rev {version.revision} · {plural(version.uploads, "upload")}</span></button>)}</div>
        </div>
        {selected && <VersionActions key={selected.versionId} version={selected} enabled={item.status === "fresh"} onApplied={refresh} />}
      </div>
      {selected && <div className="vstrip"><span>Status <b>{titleCase(selected.status)}</b></span><span>Trust <b>{selected.trust.toFixed(2)}</b></span>
        <span>Uploads <b>{selected.uploads}</b> from <b>{selected.contributors.length}</b> counted contributors</span>
        <span>Filters <b>{selected.servedFilters}</b> served of <b>{selected.filters}</b></span>
        <span>Match keys <b>{selected.matchKeys.length}</b>{selected.matchKeys.length > 0 &&
          ` (${[...new Set(selected.matchKeys.map(key => key.type.replace(/_/g, " ")))].join(", ")})`}</span>
        <span>Downloads <b>{count(selected.downloads)}</b></span><span className="faint mono">{selected.versionId}</span></div>}
      <div className="two">
        <div className="col">
          <DataState status={detail.status} onRetry={detail.refresh} />
          {detail.data && selected && <>
            <RuntimeTimeline filters={detail.data.filters} runtimeMs={selected.runtimeMs} selected={filterId} href={filterHref} revision={selected.revision} />
            <FilterTable filters={detail.data.filters} selected={filterId} href={filterHref} />
            <RevisionHistory revisions={detail.data.revisions} label={versionName(selected)} />
          </>}
        </div>
        <div className="col">
          {filterId ? <><DataState status={context.status} onRetry={context.refresh} />
            {context.data && <FilterCard context={context.data} fresh={context.status === "fresh"} onApplied={refresh} />}</> :
            <aside className="card fcard empty-card"><h2>No filter selected</h2><p className="muted">Select a filter on the timeline or in the table to see
              its votes, attesting uploads, reports and moderation.</p></aside>}
        </div>
      </div>
    </>}
  </section>;
}
