import type { ItemRouteStep } from "../routes";
import { readUserValue, writeUserValue } from "../shared/user-storage";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  fetchLibraryItem,
  fetchLibraryItemChildren,
  fetchLibraryItemSeasons,
  fetchLibraryItems,
  primaryImageUrl,
} from "../api/api-service";
import type {
  LibraryItemQuery,
  JellyfinLibrary,
  JellyfinMediaItem,
  JellyfinMediaPage,
} from "../api/api-media-types";
import {
  usePageTitle,
} from "../shared/display";
import {
  formatRating,
  MediaDuration,
} from "./media-display";
import {
  MediaDetail,
  LibraryBreadcrumbs,
  type Breadcrumb,
} from "./media-detail";
import {
  EmptyPage,
} from "../ui/EmptyPage";
import {
  navigate,
} from "../routes";
import {
  normalizedExportStatus,
  ExportBadges,
} from "../review/export-status";
import {
  applicationUrl,
} from "../shared/runtime-urls";
import { catalogPageSize, type LibraryDisplayPreferences, type LibraryView, type SortKey, type EdlFilter, type RatingFilter, type ResolutionFilter } from "./catalog-types";

const defaultLibraryDisplayPreferences: LibraryDisplayPreferences = {
  view: "icon", sort: "title", direction: "asc", filter: "all", ratingFilter: "all", resolutionFilter: "all",
};

function libraryPageCacheKey(libraryId: string, query: LibraryItemQuery): string {
  return JSON.stringify({
    libraryId,
    startIndex: query.startIndex ?? 0,
    limit: query.limit ?? 100,
    sortBy: query.sortBy ?? "title",
    sortDirection: query.sortDirection ?? "asc",
    edlFilter: query.edlFilter ?? "all",
    ratingFilter: query.ratingFilter ?? "all",
    resolutionFilter: query.resolutionFilter ?? "all",
    search: query.search?.trim() || "",
  });
}

function clearLibraryPageCache(cache: Map<string, JellyfinMediaPage>, libraryId: string): void {
  for (const key of cache.keys()) {
    if (key.includes(`"libraryId":"${libraryId}"`)) cache.delete(key);
  }
}

function libraryDisplayPreferences(libraryId: string): LibraryDisplayPreferences {
  try {
    const saved = readUserValue(`library-display.${libraryId}`);
    if (!saved) return defaultLibraryDisplayPreferences;
    return { ...defaultLibraryDisplayPreferences, ...JSON.parse(saved) };
  } catch { return defaultLibraryDisplayPreferences; }
}

/** Browse a Jellyfin library with cached filters, sorting, and view preferences. */
export function LibraryViewPage({
  library,
  cache,
}: {
  library: JellyfinLibrary;
  cache: Map<string, JellyfinMediaPage>;
}) {
  usePageTitle(library.name);
  const [items, setItems] = useState<JellyfinMediaItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [ratingValues, setRatingValues] = useState<string[]>([]);
  const [resolutionValues, setResolutionValues] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const preferences = libraryDisplayPreferences(library.id);
  const [view, setView] = useState<LibraryView>(preferences.view);
  const [search, setSearch] = useState("");
  const [deferredSearch, setDeferredSearch] = useState("");
  const [filter, setFilter] = useState<EdlFilter>(preferences.filter);
  const [ratingFilter, setRatingFilter] = useState<RatingFilter>(
    preferences.ratingFilter === "rated" || preferences.ratingFilter === "unrated"
      ? "all"
      : preferences.ratingFilter || "all",
  );
  const [resolutionFilter, setResolutionFilter] = useState<ResolutionFilter>(preferences.resolutionFilter || "all");
  const [sort, setSort] = useState<SortKey>(preferences.sort);
  const [direction, setDirection] = useState<"asc" | "desc">(preferences.direction);
  const [pageStart, setPageStart] = useState(0);
  const requestVersion = useRef(0);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDeferredSearch(search), 200);
    return () => window.clearTimeout(timeout);
  }, [search]);
  useEffect(() => { setPageStart(0); }, [library.id]);
  useEffect(() => {
    writeUserValue(
      `library-display.${library.id}`,
      JSON.stringify({ view, sort, direction, filter, ratingFilter, resolutionFilter }),
    );
  }, [direction, filter, library.id, ratingFilter, resolutionFilter, sort, view]);

  const loadItems = useCallback(async (refresh = false) => {
    const version = ++requestVersion.current;
    const query: LibraryItemQuery = {
      startIndex: pageStart,
      limit: catalogPageSize,
      sortBy: sort,
      sortDirection: direction,
      edlFilter: filter,
      ratingFilter,
      resolutionFilter,
      search: deferredSearch,
    };
    const cacheKey = libraryPageCacheKey(library.id, query);
    const isTitleSearch = Boolean(query.search?.trim());
    if (!refresh && !isTitleSearch) {
      const cachedPage = cache.get(cacheKey);
      if (cachedPage) {
        setItems(cachedPage.items);
        setTotalCount(cachedPage.total_count);
        setRatingValues(cachedPage.rating_values || []);
        setResolutionValues(cachedPage.resolution_values || []);
        setError(null);
        setLoading(false);
        return;
      }
    } else if (refresh) {
      clearLibraryPageCache(cache, library.id);
    }
    setLoading(true);
    try {
      const page = await fetchLibraryItems(library.id, { ...query, refresh });
      if (version !== requestVersion.current) return;
      if (!isTitleSearch) cache.set(cacheKey, page);
      setItems(page.items); setTotalCount(page.total_count); setRatingValues(page.rating_values || []); setResolutionValues(page.resolution_values || []); setError(null);
    } catch (reason) {
      if (version !== requestVersion.current) return;
      setError(reason instanceof Error ? reason.message : "Unable to load library items.");
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, [cache, deferredSearch, direction, filter, library.id, pageStart, ratingFilter, resolutionFilter, sort]);
  useEffect(() => { void loadItems(); }, [loadItems]);

  const hasActiveFilters = Boolean(search.trim())
    || filter !== "all"
    || ratingFilter !== "all"
    || resolutionFilter !== "all";

  const clearFilters = () => {
    setPageStart(0);
    setSearch("");
    setFilter("all");
    setRatingFilter("all");
    setResolutionFilter("all");
  };

  const toggleSort = (nextSort: SortKey) => {
    setPageStart(0);
    if (sort === nextSort) setDirection((value) => value === "asc" ? "desc" : "asc");
    else { setSort(nextSort); setDirection("asc"); }
  };
  const openItem = (item: JellyfinMediaItem) => navigate(
    item.item_type === "Series"
      ? `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(item.id)}`
      : `library/${encodeURIComponent(library.id)}/item/${encodeURIComponent(item.id)}`,
  );

  return <div className="content-page library-page">
    <header className="page-header"><div><p className="eyebrow">Library</p><h1>{library.name}</h1><p aria-live="polite">{library.collection_type || "Jellyfin library"} · {totalCount} shown</p></div></header>
    <div className="library-toolbar" aria-label="Library controls">
      <div className="library-toolbar-row">
        <button className="btn-secondary compact-button" onClick={() => void loadItems(true)} disabled={loading}>Refresh Library</button>
        <div className="segmented-control" role="group" aria-label="View">
          <button className={view === "icon" ? "selected" : ""} onClick={() => setView("icon")} aria-pressed={view === "icon"}>Icon</button>
          <button className={view === "list" ? "selected" : ""} onClick={() => setView("list")} aria-pressed={view === "list"}>List</button>
        </div>
        <div className="sort-control"><label><span>Sort</span><select aria-label="Sort items" value={sort} onChange={(event) => { setPageStart(0); setSort(event.target.value as SortKey); setDirection("asc"); }}><option value="title">Title</option><option value="year">Year</option><option value="rating">Rating</option><option value="duration">Length</option><option value="type">Type</option></select></label><button className="sort-direction" onClick={() => { setPageStart(0); setDirection((value) => value === "asc" ? "desc" : "asc"); }} aria-label={`Sort direction: ${direction === "asc" ? "ascending" : "descending"}`} title={`Sort ${direction === "asc" ? "ascending" : "descending"}`}>{direction === "asc" ? "↑" : "↓"}</button></div>
      </div>
      <div className="library-toolbar-row library-toolbar-row-filters">
        <label className="filter-control"><span>Filter</span><select aria-label="EDL filter" value={filter} onChange={(event) => { setPageStart(0); setFilter(event.target.value as EdlFilter); }}><option value="all">All items</option><option value="exported">EDL exported</option><option value="not_exported">No EDL exported</option></select></label>
        <label className="filter-control"><span>Rating</span><select aria-label="Rating filter" value={ratingFilter} onChange={(event) => { setPageStart(0); setRatingFilter(event.target.value); }}><option value="all">All ratings</option>{ratingValues.map((rating) => <option key={rating} value={rating}>{rating}</option>)}</select></label>
        <label className="filter-control"><span>Resolution</span><select aria-label="Resolution filter" value={resolutionFilter} onChange={(event) => { setPageStart(0); setResolutionFilter(event.target.value); }}><option value="all">All resolutions</option>{resolutionValues.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}</select></label>
        <div className="library-toolbar-right">
          <label className="search-control"><span className="visually-hidden">Search titles</span><input type="search" value={search} onChange={(event) => { setPageStart(0); setSearch(event.target.value); }} placeholder="Search titles" /></label>
          {hasActiveFilters && <button className="btn-secondary compact-button" onClick={clearFilters} disabled={loading}>Clear Filters</button>}
        </div>
      </div>
    </div>
    {loading && <p className="loading-state">Loading library…</p>}
    {error && <div className="load-error" role="alert"><span>{error}</span><button className="btn-secondary" onClick={() => void loadItems()}>Retry</button></div>}
    {!loading && !error && (view === "icon" ? <div className="poster-grid library-poster-grid">{items.map((item) => <MediaCard key={item.id} item={item} onOpen={() => openItem(item)} />)}</div> : <div className="library-table-wrap"><table className="library-table"><thead><tr><SortableHeader label="Title" active={sort === "title"} direction={direction} onClick={() => toggleSort("title")} /><SortableHeader label="Year" active={sort === "year"} direction={direction} onClick={() => toggleSort("year")} /><th>Resolution</th><SortableHeader label="Length" active={sort === "duration"} direction={direction} onClick={() => toggleSort("duration")} /><SortableHeader label="Rating" active={sort === "rating"} direction={direction} onClick={() => toggleSort("rating")} /><SortableHeader label="Type" active={sort === "type"} direction={direction} onClick={() => toggleSort("type")} /><th>Exports</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><button className="item-link" onClick={() => openItem(item)}>{item.name}</button></td><td>{item.year || "—"}</td><td>{item.resolution || "—"}</td><td><MediaDuration seconds={item.duration_seconds} /></td><td>{formatRating(item)}</td><td>{item.item_type}</td><td><ExportBadges status={normalizedExportStatus(item)} /></td></tr>)}</tbody></table></div>)}
    {!loading && !error && !items.length && <EmptyPage title="No matching items" body="Try a different search term or EDL filter." />}
    {!loading && !error && totalCount > catalogPageSize && <nav className="library-pagination" aria-label="Library pagination"><span aria-live="polite">Showing {pageStart + 1}–{Math.min(pageStart + items.length, totalCount)} of {totalCount}</span><button className="btn-secondary compact-button" onClick={() => setPageStart((value) => Math.max(0, value - catalogPageSize))} disabled={pageStart === 0}>Previous</button><button className="btn-secondary compact-button" onClick={() => setPageStart((value) => value + catalogPageSize)} disabled={pageStart + items.length >= totalCount}>Next</button></nav>}
  </div>;
}

function SortableHeader({ label, active, direction, onClick }: { label: string; active: boolean; direction: "asc" | "desc"; onClick: () => void }) {
  return <th aria-sort={active ? direction === "asc" ? "ascending" : "descending" : "none"}><button className="sort-button" onClick={onClick}>{label}{active && <span aria-hidden="true"> {direction === "asc" ? "↑" : "↓"}</span>}</button></th>;
}

function MediaCard({ item, onOpen }: { item: JellyfinMediaItem; onOpen: () => void }) {
  const imageTag = typeof item.image_tags.Primary === "string" ? item.image_tags.Primary : undefined;
  const imageUrl = item.image_url ? applicationUrl(item.image_url) : (imageTag ? primaryImageUrl(item.id, imageTag) : null);
  return <button className="media-card" onClick={onOpen} aria-label={`Open ${item.name}`}>
    <div className="poster-frame">{imageUrl ? <img src={imageUrl} alt="" loading="lazy" /> : <span className="poster-fallback" aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span>}<ExportBadges status={normalizedExportStatus(item)} poster /></div>
    <span className="media-title">{item.name}</span><span className="media-meta">{item.year || item.item_type}</span>
  </button>;
}

/** Load a Jellyfin item and render its detail workspace. */
export function MediaDetailPage({
  library,
  itemId,
  seriesId,
  seasonId,
  onNavigate,
  onDirtyChange,
  initialStep,
}: {
  library: JellyfinLibrary;
  itemId: string;
  seriesId?: string;
  seasonId?: string;
  onNavigate: (route: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  initialStep?: ItemRouteStep;
}) {
  const [item, setItem] = useState<JellyfinMediaItem | null>(null);
  const [series, setSeries] = useState<JellyfinMediaItem | null>(null);
  const [season, setSeason] = useState<JellyfinMediaItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(item?.name || library.name);
  useEffect(() => {
    let active = true;
    const requests = [fetchLibraryItem(itemId)];
    if (seriesId) requests.push(fetchLibraryItem(seriesId));
    if (seasonId) requests.push(fetchLibraryItem(seasonId));
    void Promise.all(requests).then((loaded) => {
      if (!active) return;
      let parentIndex = 1;
      setItem(loaded[0]);
      setSeries(seriesId ? loaded[parentIndex++] : null);
      setSeason(seasonId ? loaded[parentIndex] : null);
      setError(null);
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "Unable to load library item.");
    });
    return () => { active = false; };
  }, [itemId, seasonId, seriesId]);
  const libraryRoute = `library/${encodeURIComponent(library.id)}`;
  if (error) return <div className="content-page"><div className="load-error" role="alert"><span>{error}</span><button className="btn-secondary" onClick={() => onNavigate(libraryRoute)}>Back to library</button></div></div>;
  if (!item || item.id !== itemId) return <div className="content-page"><p className="loading-state">Loading item…</p></div>;
  const breadcrumbs: Breadcrumb[] = [
    { label: library.name, route: libraryRoute },
    ...(series && seriesId ? [{ label: series.name, route: `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}` }] : []),
    ...(season && seriesId && seasonId ? [{ label: season.name, route: `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}/season/${encodeURIComponent(seasonId)}` }] : []),
    { label: item.name },
  ];
  return <MediaDetail key={item.id} item={item} breadcrumbs={breadcrumbs} onNavigate={onNavigate} seasonNumber={season?.index_number ?? item.season_number} onDirtyChange={onDirtyChange} initialStep={initialStep} />;
}

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
  return <SeasonBrowser library={library} seriesId={seriesId} series={series} seasons={seasons} />;
}

/** Load child seasons or episodes while preserving navigation context. */
function SeasonBrowser({
  library,
  seriesId,
  series,
  seasons,
}: {
  library: JellyfinLibrary;
  seriesId: string;
  series: JellyfinMediaItem;
  seasons: JellyfinMediaItem[];
}) {
  const [view, setView] = useState<LibraryView>("icon");
  const [itemSize, setItemSize] = useState(180);
  const [sort, setSort] = useState<"title" | "number">("number");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const orderedSeasons = [...seasons].sort((left, right) => {
    const comparison = sort === "number"
      ? (left.index_number || 0) - (right.index_number || 0) || left.name.localeCompare(right.name)
      : left.name.localeCompare(right.name);
    return direction === "asc" ? comparison : -comparison;
  });
  const openSeason = (season: JellyfinMediaItem) => navigate(
    `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}/season/${encodeURIComponent(season.id)}`,
  );
  const toggleSort = (nextSort: "title" | "number") => {
    if (sort === nextSort) setDirection((value) => value === "asc" ? "desc" : "asc");
    else { setSort(nextSort); setDirection("asc"); }
  };
  return <div className="content-page library-page"><LibraryBreadcrumbs crumbs={[{ label: library.name, route: `library/${encodeURIComponent(library.id)}` }, { label: series.name }]} /><header className="page-header"><div><p className="eyebrow">Series</p><h1>{series.name}</h1><p>{seasons.length} seasons</p></div></header>
    <div className="library-toolbar" aria-label="Season controls">
      <div className="segmented-control" role="group" aria-label="Season view">
        <button className={view === "icon" ? "selected" : ""} onClick={() => setView("icon")} aria-pressed={view === "icon"}>Icon</button>
        <button className={view === "list" ? "selected" : ""} onClick={() => setView("list")} aria-pressed={view === "list"}>List</button>
      </div>
      <label className="size-control"><span>Size</span><input type="range" min="120" max="280" value={itemSize} onChange={(event) => setItemSize(Number(event.target.value))} aria-label="Season item size" /></label>
      <div className="sort-control"><label><span>Sort</span><select aria-label="Sort seasons" value={sort} onChange={(event) => { setSort(event.target.value as "title" | "number"); setDirection("asc"); }}><option value="number">Season number</option><option value="title">Title</option></select></label><button className="sort-direction" onClick={() => setDirection((value) => value === "asc" ? "desc" : "asc")} aria-label={`Sort direction: ${direction === "asc" ? "ascending" : "descending"}`}>{direction === "asc" ? "↑" : "↓"}</button></div>
    </div>
    {orderedSeasons.length ? (view === "icon" ? <div className="poster-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${itemSize}px, 1fr))` }}>{orderedSeasons.map((season) => <MediaCard key={season.id} item={season} onOpen={() => openSeason(season)} />)}</div> : <table className="library-table" style={{ "--library-row-size": `${Math.round(itemSize / 4)}px` } as React.CSSProperties}><thead><tr><SortableHeader label="No." active={sort === "number"} direction={direction} onClick={() => toggleSort("number")} /><SortableHeader label="Season" active={sort === "title"} direction={direction} onClick={() => toggleSort("title")} /><th>Rating</th><th>Exports</th></tr></thead><tbody>{orderedSeasons.map((season) => <tr key={season.id}><td>{season.index_number ?? "—"}</td><td><button className="item-link" onClick={() => openSeason(season)}>{season.name}</button></td><td>{formatRating(season)}</td><td><ExportBadges status={normalizedExportStatus(season)} /></td></tr>)}</tbody></table>) : <EmptyPage title="No seasons available" body="Jellyfin did not return any seasons for this series." />}
  </div>;
}

export function SeasonEpisodesPage({ library, seriesId, seasonId }: { library: JellyfinLibrary; seriesId: string; seasonId: string }) {
  const [season, setSeason] = useState<JellyfinMediaItem | null>(null);
  const [series, setSeries] = useState<JellyfinMediaItem | null>(null);
  const [episodes, setEpisodes] = useState<JellyfinMediaItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  usePageTitle(season?.name || library.name);
  useEffect(() => {
    let active = true;
    void Promise.all([fetchLibraryItem(seasonId), fetchLibraryItemChildren(seasonId), fetchLibraryItem(seriesId)]).then(([parent, children, parentSeries]) => {
      if (active) { setSeason(parent); setSeries(parentSeries); setEpisodes(children.filter((item) => item.item_type === "Episode" || item.item_type === "Video").sort((left, right) => (left.index_number || 0) - (right.index_number || 0) || left.name.localeCompare(right.name))); setError(null); }
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "Unable to load episodes."); });
    return () => { active = false; };
  }, [seasonId, seriesId]);
  if (error) return <EmptyPage title="Episodes unavailable" body={error} />;
  if (!season) return <div className="content-page"><p className="loading-state">Loading episodes…</p></div>;
  return <div className="content-page library-page"><LibraryBreadcrumbs crumbs={[{ label: library.name, route: `library/${encodeURIComponent(library.id)}` }, { label: series?.name || "Series", route: `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}` }, { label: season.name }]} /><header className="page-header"><div><p className="eyebrow">Season</p><h1>{season.name}</h1><p>{episodes.length} episodes</p></div></header>{episodes.length ? <table className="library-table"><thead><tr><th>No.</th><th>Episode</th><th>Year</th><th>Rating</th><th>Exports</th></tr></thead><tbody>{episodes.map((episode) => <tr key={episode.id}><td>{episode.index_number ?? "—"}</td><td><button className="item-link" onClick={() => navigate(`library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(seriesId)}/season/${encodeURIComponent(seasonId)}/item/${encodeURIComponent(episode.id)}`)}>{episode.name}</button></td><td>{episode.year || "—"}</td><td>{formatRating(episode)}</td><td><ExportBadges status={normalizedExportStatus(episode)} /></td></tr>)}</tbody></table> : <EmptyPage title="No episodes available" body="Jellyfin did not return any episodes for this season." />}</div>;
}

