import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JellyfinLibrary, JellyfinMediaItem, JellyfinMediaPage } from "../api/api-media-types";
import { navigate } from "../routes";
import { usePageTitle } from "../shared/display";
import { readUserValue, writeUserValue } from "../shared/user-storage";
import { EmptyPage } from "../ui/EmptyPage";
import { LoadMoreFooter } from "../ui/LoadMoreFooter";
import { AlphabetRail, useCurrentLetter } from "./alphabet-rail";
import { librarySortLabels, type LibraryDisplayPreferences, type LibraryView, type SortDirection, type SortKey } from "./catalog-types";
import { PosterSizeControl, RefreshButton, SortMenu, ViewToggle, usePosterSize, useStickyToolbar } from "./library-controls";
import { LibrarySearch } from "./library-search";
import { activeTokens, searchSuggestions, type LibraryFilters, type SearchSuggestion } from "./library-search-suggestions";
import { LibraryTable } from "./library-table";
import { MediaCard, PosterGrid } from "./media-card";
import { letterName } from "./title-letters";
import { useLibraryItems } from "./use-library-items";

const defaultLibraryDisplayPreferences: LibraryDisplayPreferences = {
  view: "icon", sort: "title", direction: "asc", filter: "all", ratingFilter: "all", resolutionFilter: "all",
};
const sortOptions = (Object.keys(librarySortLabels) as SortKey[]).map((key) => ({ key, label: librarySortLabels[key] }));

function libraryDisplayPreferences(libraryId: string): LibraryDisplayPreferences {
  try {
    const saved = readUserValue(`library-display.${libraryId}`);
    if (!saved) return defaultLibraryDisplayPreferences;
    const parsed = { ...defaultLibraryDisplayPreferences, ...JSON.parse(saved) };
    // Retired "rated"/"unrated" rating filters fall back to every rating.
    return { ...parsed, ratingFilter: ["rated", "unrated"].includes(parsed.ratingFilter) ? "all" : parsed.ratingFilter || "all" };
  } catch { return defaultLibraryDisplayPreferences; }
}

/**
 * Browse a Jellyfin library as one continuously loading poster grid or table. A single search box takes title
 * text and filter tokens, one menu sets the sort, and under title sorting an A–Z rail jumps between letters.
 */
export function LibraryViewPage({ library, cache }: { library: JellyfinLibrary; cache: Map<string, JellyfinMediaPage> }) {
  usePageTitle(library.name);
  const [preferences] = useState(() => libraryDisplayPreferences(library.id));
  const [view, setView] = useState<LibraryView>(preferences.view);
  const [search, setSearch] = useState("");
  const [deferredSearch, setDeferredSearch] = useState("");
  const [filters, setFilters] = useState<LibraryFilters>({
    filter: preferences.filter, ratingFilter: preferences.ratingFilter, resolutionFilter: preferences.resolutionFilter || "all",
  });
  const [sort, setSort] = useState<SortKey>(preferences.sort);
  const [direction, setDirection] = useState<SortDirection>(preferences.direction);
  const [posterSize, setPosterSize] = usePosterSize();
  const [jumpLetter, setJumpLetter] = useState<string | null>(null);
  const [scrollTarget, setScrollTarget] = useState<number | null>(null);
  const [anchorId, setAnchorId] = useState<string | null>(null);
  const toolbar = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const { height: stickyHeight, style: stickyStyle } = useStickyToolbar(toolbar);

  useEffect(() => {
    const timeout = window.setTimeout(() => setDeferredSearch(search), 200);
    return () => window.clearTimeout(timeout);
  }, [search]);
  useEffect(() => {
    writeUserValue(`library-display.${library.id}`, JSON.stringify({ view, sort, direction, ...filters }));
  }, [direction, filters, library.id, sort, view]);

  const query = useMemo(() => ({ sort, direction, ...filters, search: deferredSearch }), [deferredSearch, direction, filters, sort]);
  const listing = useLibraryItems(library.id, query, cache);
  const { items, totalCount, letterOffsets } = listing;
  const railVisible = sort === "title" && items.length > 0 && Object.keys(letterOffsets).length > 0;
  const currentLetter = useCurrentLetter(content, items, railVisible, stickyHeight, anchorId);

  const openItem = useCallback((item: JellyfinMediaItem) => navigate(
    item.item_type === "Series"
      ? `library/${encodeURIComponent(library.id)}/series/${encodeURIComponent(item.id)}`
      : `library/${encodeURIComponent(library.id)}/item/${encodeURIComponent(item.id)}`,
  ), [library.id]);
  const searchSettled = search.trim() === deferredSearch.trim() && !listing.loading;
  const suggestions = useMemo(() => searchSuggestions(search, filters,
    { ratings: listing.ratingValues, resolutions: listing.resolutionValues }, searchSettled && search.trim() ? items : []),
  [filters, items, listing.ratingValues, listing.resolutionValues, search, searchSettled]);

  const updateSearch = (text: string, immediate = false) => { setSearch(text); if (immediate) setDeferredSearch(text); };
  const choose = (suggestion: SearchSuggestion) => {
    if (suggestion.kind === "title") { openItem(suggestion.item); return; }
    setFilters((current) => ({ ...current, [suggestion.token.facet]: suggestion.token.value }));
    updateSearch("", true);
  };
  const changeSort = (nextSort: SortKey, nextDirection: SortDirection) => { setSort(nextSort); setDirection(nextDirection); };
  const toggleSort = (nextSort: SortKey) => changeSort(nextSort, sort === nextSort && direction === "asc" ? "desc" : "asc");

  const jump = async (letter: string) => {
    const offset = letterOffsets[letter];
    if (offset === undefined) return;
    setJumpLetter(letter);
    const loaded = await listing.ensureLoaded(offset);
    setJumpLetter(null);
    if (loaded) setScrollTarget(offset);
  };
  useEffect(() => {
    if (scrollTarget === null) return;
    const target = content.current?.querySelector<HTMLElement>(`[data-catalog-index="${scrollTarget}"]`);
    if (!target) return;
    setAnchorId(items[scrollTarget]?.id ?? null);
    target.scrollIntoView?.({ block: "start" });
    target.focus({ preventScroll: true });
    setScrollTarget(null);
  }, [items, scrollTarget]);

  const firstLoad = listing.loading && !items.length;
  return <div className="content-page library-page catalog-page" style={stickyStyle}>
    <header className="page-header catalog-header">
      <div><p className="eyebrow">Library</p><h1>{library.name}</h1></div>
      <div className="catalog-header-actions">
        {!firstLoad && <span className="catalog-count">{totalCount} {totalCount === 1 ? "title" : "titles"}</span>}
        <RefreshButton label="Refresh library" busy={listing.loading} onRefresh={listing.refresh} />
      </div>
    </header>
    <div ref={toolbar} className="library-toolbar catalog-toolbar" role="group" aria-label="Library controls">
      <LibrarySearch text={search} onTextChange={(text) => updateSearch(text)} tokens={activeTokens(filters)}
        onRemoveToken={(token) => setFilters((current) => ({ ...current, [token.facet]: "all" }))}
        suggestions={suggestions} onChoose={choose}
        onClear={() => { updateSearch("", true); setFilters({ filter: "all", ratingFilter: "all", resolutionFilter: "all" }); }} />
      <div className="catalog-toolbar-actions">
        <SortMenu options={sortOptions} sort={sort} direction={direction} onChange={changeSort} />
        <ViewToggle view={view} onChange={setView} />
        {view === "icon" && <PosterSizeControl size={posterSize} onChange={setPosterSize} />}
      </div>
    </div>
    {firstLoad && <p className="loading-state">Loading library…</p>}
    {!listing.loading && !listing.error && !items.length && <EmptyPage title="No matching items" body="Try a different search or remove a filter." />}
    {items.length > 0 && <div ref={content} className={`catalog-content${railVisible ? " with-rail" : ""}`} aria-busy={listing.loading}>
      <div className="catalog-results">
        {view === "icon"
          ? <PosterGrid size={posterSize} label={library.name}>{items.map((item, index) => <MediaCard key={item.id} item={item} index={index} onOpen={() => openItem(item)} />)}</PosterGrid>
          : <LibraryTable items={items} sort={sort} direction={direction} onSort={toggleSort} onOpen={openItem} />}
        <LoadMoreFooter loaded={items.length} total={totalCount} loading={listing.loadingMore || jumpLetter !== null}
          complete={listing.complete} onLoadMore={listing.loadMore}
          status={jumpLetter ? `Loading titles up to ${letterName(jumpLetter)}…` : null} />
      </div>
      {railVisible && <AlphabetRail offsets={letterOffsets} direction={direction} current={currentLetter} pending={jumpLetter} onJump={(letter) => void jump(letter)} />}
    </div>}
    {listing.error && <div className="load-error" role="alert"><span>{listing.error}</span><button className="btn-secondary" onClick={listing.retry}>Retry</button></div>}
  </div>;
}
