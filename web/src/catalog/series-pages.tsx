import { useEffect, useRef, useState, type CSSProperties } from "react";
import { fetchLibraryItem, fetchLibraryItemChildren, fetchLibraryItemSeasons } from "../api/api-service";
import type { JellyfinLibrary, JellyfinMediaItem } from "../api/api-media-types";
import { ExportBadges, normalizedExportStatus } from "../review/export-status";
import { navigate } from "../routes";
import { usePageTitle } from "../shared/display";
import { EmptyPage } from "../ui/EmptyPage";
import type { LibraryView, SortDirection } from "./catalog-types";
import { PosterSizeControl, SortMenu, ViewToggle, usePosterSize, useStickyToolbar } from "./library-controls";
import { SortableHeader } from "./library-table";
import { LibraryBreadcrumbs, type Breadcrumb } from "./media-detail";
import { formatRating } from "./media-display";
import { MediaCard, PosterGrid } from "./media-card";

type ChildSort = "number" | "title";

/**
 * The seasons of a series or the episodes of a season, as poster cards or a table, with the library's compact
 * view, poster-size and sort controls.
 * @param noun Singular child name ("Season" or "Episode") used in labels.
 * @param wide Show landscape stills instead of portrait posters.
 */
function ChildBrowser({ eyebrow, title, crumbs, noun, items, wide, onOpen, empty }: {
  eyebrow: string;
  title: string;
  crumbs: Breadcrumb[];
  noun: string;
  items: JellyfinMediaItem[];
  wide: boolean;
  onOpen: (item: JellyfinMediaItem) => void;
  empty: { title: string; body: string };
}) {
  const [view, setView] = useState<LibraryView>("icon");
  const [posterSize, setPosterSize] = usePosterSize();
  const [sort, setSort] = useState<ChildSort>("number");
  const [direction, setDirection] = useState<SortDirection>("asc");
  const toolbar = useRef<HTMLDivElement>(null);
  const { style } = useStickyToolbar(toolbar);
  const ordered = [...items].sort((left, right) => {
    const comparison = sort === "number"
      ? (left.index_number || 0) - (right.index_number || 0) || left.name.localeCompare(right.name)
      : left.name.localeCompare(right.name);
    return direction === "asc" ? comparison : -comparison;
  });
  const toggleSort = (next: ChildSort) => {
    setDirection(sort === next && direction === "asc" ? "desc" : "asc");
    setSort(next);
  };
  const plural = `${noun.toLowerCase()}s`;
  return <div className="content-page library-page catalog-page" style={style}>
    <LibraryBreadcrumbs crumbs={crumbs} />
    <header className="page-header catalog-header">
      <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>
      <div className="catalog-header-actions"><span className="catalog-count">{items.length} {items.length === 1 ? noun.toLowerCase() : plural}</span></div>
    </header>
    {ordered.length ? <>
      <div ref={toolbar} className="library-toolbar catalog-toolbar" role="group" aria-label={`${noun} controls`}>
        <div className="catalog-toolbar-actions">
          <SortMenu options={[{ key: "number", label: `${noun} number` }, { key: "title", label: "Title" }]} sort={sort} direction={direction}
            onChange={(nextSort, nextDirection) => { setSort(nextSort); setDirection(nextDirection); }} />
          <ViewToggle view={view} onChange={setView} />
          {view === "icon" && <PosterSizeControl size={posterSize} onChange={setPosterSize} />}
        </div>
      </div>
      {view === "icon"
        ? <PosterGrid size={posterSize} wide={wide} label={`${title} ${plural}`}>{ordered.map((item) => <MediaCard key={item.id} item={item} wide={wide} onOpen={() => onOpen(item)} />)}</PosterGrid>
        : <div className="library-table-wrap"><table className="library-table child-table" style={{ "--library-row-size": `${Math.round(posterSize / 4)}px` } as CSSProperties}>
          <thead><tr><SortableHeader label="No." active={sort === "number"} direction={direction} onClick={() => toggleSort("number")} /><SortableHeader label={noun} active={sort === "title"} direction={direction} onClick={() => toggleSort("title")} /><th>Year</th><th>Rating</th><th>Exports</th></tr></thead>
          <tbody>{ordered.map((item) => <tr key={item.id}><td>{item.index_number ?? "—"}</td><td><button className="item-link" onClick={() => onOpen(item)}>{item.name}</button></td><td>{item.year || "—"}</td><td>{formatRating(item)}</td><td><ExportBadges status={normalizedExportStatus(item)} /></td></tr>)}</tbody>
        </table></div>}
    </> : <EmptyPage title={empty.title} body={empty.body} />}
  </div>;
}

const libraryRoute = (library: JellyfinLibrary) => `library/${encodeURIComponent(library.id)}`;
const seriesRoute = (library: JellyfinLibrary, seriesId: string) => `${libraryRoute(library)}/series/${encodeURIComponent(seriesId)}`;

/** Load a series and browse its seasons. */
export function SeriesSeasonsPage({ library, seriesId }: { library: JellyfinLibrary; seriesId: string }) {
  const [series, setSeries] = useState<JellyfinMediaItem | null>(null);
  const [seasons, setSeasons] = useState<JellyfinMediaItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(series?.name || library.name);
  useEffect(() => {
    let active = true;
    void Promise.all([fetchLibraryItem(seriesId), fetchLibraryItemSeasons(seriesId)]).then(([parent, children]) => {
      if (active) { setSeries(parent); setSeasons(children.filter((item) => item.item_type === "Season")); setError(null); }
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load seasons."); });
    return () => { active = false; };
  }, [seriesId]);
  if (error) return <EmptyPage title="Seasons unavailable" body={error} />;
  if (!series) return <div className="content-page"><p className="loading-state">Loading seasons…</p></div>;
  return <ChildBrowser eyebrow="Series" title={series.name} noun="Season" items={seasons} wide={false}
    crumbs={[{ label: library.name, route: libraryRoute(library) }, { label: series.name }]}
    onOpen={(season) => navigate(`${seriesRoute(library, seriesId)}/season/${encodeURIComponent(season.id)}`)}
    empty={{ title: "No seasons available", body: "Jellyfin did not return any seasons for this series." }} />;
}

/** Load a season and browse its episodes. */
export function SeasonEpisodesPage({ library, seriesId, seasonId }: { library: JellyfinLibrary; seriesId: string; seasonId: string }) {
  const [season, setSeason] = useState<JellyfinMediaItem | null>(null);
  const [series, setSeries] = useState<JellyfinMediaItem | null>(null);
  const [episodes, setEpisodes] = useState<JellyfinMediaItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(season?.name || library.name);
  useEffect(() => {
    let active = true;
    void Promise.all([fetchLibraryItem(seasonId), fetchLibraryItemChildren(seasonId), fetchLibraryItem(seriesId)]).then(([parent, children, parentSeries]) => {
      if (active) { setSeason(parent); setSeries(parentSeries); setEpisodes(children.filter((item) => item.item_type === "Episode" || item.item_type === "Video")); setError(null); }
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load episodes."); });
    return () => { active = false; };
  }, [seasonId, seriesId]);
  if (error) return <EmptyPage title="Episodes unavailable" body={error} />;
  if (!season) return <div className="content-page"><p className="loading-state">Loading episodes…</p></div>;
  return <ChildBrowser eyebrow="Season" title={season.name} noun="Episode" items={episodes} wide
    crumbs={[{ label: library.name, route: libraryRoute(library) }, { label: series?.name || "Series", route: seriesRoute(library, seriesId) }, { label: season.name }]}
    onOpen={(episode) => navigate(`${seriesRoute(library, seriesId)}/season/${encodeURIComponent(seasonId)}/item/${encodeURIComponent(episode.id)}`)}
    empty={{ title: "No episodes available", body: "Jellyfin did not return any episodes for this season." }} />;
}
