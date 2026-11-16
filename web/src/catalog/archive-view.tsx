import { useEffect, useState } from "react";
import { fetchArchive, type ArchivePage, type ArchiveSource, type ArchivedItem } from "../api/api-archive";
import { usePageTitle } from "../shared/display";
import "../styles/archive.css";

const SOURCE_LABELS: Record<ArchiveSource, string> = { radarr: "Radarr", sonarr: "Sonarr", manual_files: "Other Files" };

const when = (value: string) => new Date(value).toLocaleString();
const pad = (value: number) => String(value).padStart(2, "0");

function episodeLabel(item: ArchivedItem): string | null {
  if (item.season === null && item.episode === null) return null;
  const episodes = item.episode === null ? null
    : item.last_episode === null ? `E${pad(item.episode)}` : `E${pad(item.episode)}-E${pad(item.last_episode)}`;
  return [item.season !== null && `S${pad(item.season)}`, episodes].filter(Boolean).join(" ");
}

const PAGE_SIZE = 20;

type ArchiveState = { kind: "loading" } | { kind: "error"; message: string } | { kind: "loaded"; page: ArchivePage };

/** Read-only record of media that came from removed catalog sources. */
export function ArchiveView() {
  usePageTitle("Archive");
  const [offset, setOffset] = useState(0);
  const [state, setState] = useState<ArchiveState>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "loading" });
    fetchArchive(offset, PAGE_SIZE, controller.signal)
      .then(page => {
        if (controller.signal.aborted) return;
        if (page.offset > 0 && page.offset >= page.total) setOffset(Math.max(0, Math.floor((page.total - 1) / page.limit) * page.limit));
        else setState({ kind: "loaded", page });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setState({ kind: "error", message: reason instanceof Error ? reason.message : "Unable to load the archive." });
      });
    return () => controller.abort();
  }, [offset]);
  return <div className="content-page archive-page">
    <header className="page-header"><div><h1>Archive</h1><p>Scans, reviews, and exports kept from media sources Editarr no longer connects to.</p></div></header>
    {state.kind === "loading" && <p role="status" aria-busy="true">Loading archive...</p>}
    {state.kind === "error" && <p role="alert">{state.message}</p>}
    {state.kind === "loaded" && (state.page.total === 0
      ? <p className="muted-copy">No archived media.</p>
      : <>
        <ul className="archive-list">{state.page.items.map(item => <ArchiveEntry key={`${item.source}:${item.id}`} item={item} />)}</ul>
        {state.page.total > state.page.limit && <ArchivePager page={state.page} onOffset={setOffset} />}
      </>)}
  </div>;
}

function ArchivePager({ page, onOffset }: { page: ArchivePage; onOffset: (offset: number) => void }) {
  const last = Math.min(page.offset + page.items.length, page.total);
  return <nav className="archive-pagination" aria-label="Archive pages">
    <span>{`${page.offset + 1}-${last} of ${page.total}`}</span>
    <div className="button-row">
      <button className="btn-secondary" disabled={page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - page.limit))}>Previous page</button>
      <button className="btn-secondary" disabled={last >= page.total} onClick={() => onOffset(page.offset + page.limit)}>Next page</button>
    </div>
  </nav>;
}

function ArchiveEntry({ item }: { item: ArchivedItem }) {
  const episode = episodeLabel(item);
  return <li className="settings-card archive-item">
    <div>
      <p className="eyebrow">{SOURCE_LABELS[item.source]} · {item.item_type}</p>
      {item.series && <p className="archive-series">{item.series}</p>}
      <h2>{item.title}{episode && <span className="archive-episode"> {episode}</span>}</h2>
      {item.path && <p className="archive-path">{item.path}</p>}
    </div>
    <ArchiveSection title="Scans" empty="No scans." rows={item.scans.map(scan => ({
      key: scan.id,
      cells: [`Scan #${scan.scan_number}`, scan.name || "Unnamed", scan.status],
      date: scan.created_at,
    }))} />
    <ArchiveSection title="Reviews" empty="No reviews." rows={item.reviews.map(review => ({
      key: review.id,
      cells: [review.name, review.owner || "Unknown owner"],
      date: review.updated_at,
    }))} />
    <ArchiveSection title="Exports" empty="No exports." rows={item.exports.map(published => ({
      key: String(published.id),
      cells: [published.format.toUpperCase(), published.scope],
      date: published.exported_at,
    }))} />
  </li>;
}

function ArchiveSection({ title, empty, rows }: { title: string; empty: string; rows: { key: string; cells: string[]; date: string }[] }) {
  return <section className="archive-section" aria-label={title}>
    <h3>{title}</h3>
    {rows.length === 0 ? <p className="muted-copy">{empty}</p> : <ul>{rows.map(row => <li key={row.key}>
      {row.cells.map((cell, index) => <span key={index}>{cell}</span>)}
      <time dateTime={row.date}>{when(row.date)}</time>
    </li>)}</ul>}
  </section>;
}
