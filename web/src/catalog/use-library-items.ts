import { useCallback, useEffect, useRef, useState } from "react";
import { fetchLibraryItems } from "../api/api-service";
import type { JellyfinMediaItem, JellyfinMediaPage, LibraryItemQuery } from "../api/api-media-types";
import { catalogMaxRequestSize, catalogPageSize, type LibraryListingQuery } from "./catalog-types";

export interface LibraryListing {
  items: JellyfinMediaItem[];
  totalCount: number;
  ratingValues: string[];
  resolutionValues: string[];
  /** Index of each initial's first item under title sorting. */
  letterOffsets: Record<string, number>;
  /** The first page for the current query is loading. */
  loading: boolean;
  /** A further page is loading. */
  loadingMore: boolean;
  error: string | null;
  /** Whether every matching item has been loaded. */
  complete: boolean;
  /** Load the next page unless one is already loading or nothing remains. */
  loadMore: () => void;
  /** Load pages until `index` is loaded; resolves whether it now is. */
  ensureLoaded: (index: number) => Promise<boolean>;
  /** Reload from the first page, asking the server to refresh its catalog. */
  refresh: () => void;
  /** Retry the request that failed. */
  retry: () => void;
}

interface Progress {
  generation: number;
  items: JellyfinMediaItem[];
  nextStart: number;
  complete: boolean;
  failed: boolean;
  busy: Promise<void> | null;
}

const emptyState = {
  items: [] as JellyfinMediaItem[], totalCount: 0, ratingValues: [] as string[], resolutionValues: [] as string[],
  letterOffsets: {} as Record<string, number>, loading: true, loadingMore: false, error: null as string | null, complete: false,
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

/**
 * Load one library listing continuously: the first page for `query`, then further server pages on request.
 * Changing the library or query starts over, discarding late responses; only one request runs at a time, so
 * repeated requests never load a page twice. Pages without a title search are kept in `cache`.
 */
export function useLibraryItems(libraryId: string, query: LibraryListingQuery, cache: Map<string, JellyfinMediaPage>): LibraryListing {
  const [state, setState] = useState(emptyState);
  const progress = useRef<Progress>({ generation: 0, items: [], nextStart: 0, complete: false, failed: false, busy: null });
  const { sort, direction, filter, ratingFilter, resolutionFilter, search } = query;

  const fetchChunk = useCallback(async (startIndex: number, limit: number, refresh: boolean) => {
    const request: LibraryItemQuery = {
      startIndex, limit, sortBy: sort, sortDirection: direction, edlFilter: filter, ratingFilter, resolutionFilter, search,
    };
    const cacheKey = libraryPageCacheKey(libraryId, request);
    const cacheable = !search.trim();
    if (refresh) clearLibraryPageCache(cache, libraryId);
    else if (cacheable) {
      const cached = cache.get(cacheKey);
      if (cached) return cached;
    }
    const page = await fetchLibraryItems(libraryId, { ...request, refresh });
    if (cacheable) cache.set(cacheKey, page);
    return page;
  }, [cache, direction, filter, libraryId, ratingFilter, resolutionFilter, search, sort]);

  const enqueue = useCallback((task: (generation: number) => Promise<void> | void) => {
    const current = progress.current;
    const generation = current.generation;
    const run = (current.busy ?? Promise.resolve()).then(() => generation === current.generation ? task(generation) : undefined);
    const tracked: Promise<void> = run.finally(() => { if (current.busy === tracked) current.busy = null; });
    current.busy = tracked;
    return tracked;
  }, []);

  const appendChunk = useCallback(async (generation: number, limit: number, refresh = false) => {
    const current = progress.current;
    const start = current.nextStart;
    setState((previous) => ({ ...previous, loading: start === 0, loadingMore: start > 0, error: null }));
    try {
      const page = await fetchChunk(start, limit, refresh);
      if (generation !== current.generation) return;
      const known = new Set(start === 0 ? [] : current.items.map((item) => item.id));
      current.items = [...(start === 0 ? [] : current.items), ...page.items.filter((item) => !known.has(item.id))];
      current.nextStart = start + page.items.length;
      current.complete = !page.items.length || current.nextStart >= page.total_count;
      setState({
        items: current.items,
        totalCount: page.total_count,
        ratingValues: page.rating_values || [],
        resolutionValues: page.resolution_values || [],
        letterOffsets: page.title_letter_offsets || {},
        loading: false,
        loadingMore: false,
        error: null,
        complete: current.complete,
      });
    } catch (reason) {
      if (generation !== current.generation) return;
      current.failed = true;
      setState((previous) => ({
        ...previous, loading: false, loadingMore: false,
        error: reason instanceof Error ? reason.message : "Unable to load library items.",
      }));
    }
  }, [fetchChunk]);

  const restart = useCallback((refresh: boolean) => {
    const current = progress.current;
    Object.assign(current, { generation: current.generation + 1, items: [], nextStart: 0, complete: false, failed: false, busy: null });
    void enqueue((generation) => appendChunk(generation, catalogPageSize, refresh));
  }, [appendChunk, enqueue]);

  useEffect(() => { restart(false); }, [restart]);

  const loadMore = useCallback(() => {
    const current = progress.current;
    if (current.busy || current.complete || current.failed) return;
    void enqueue((generation) => appendChunk(generation, catalogPageSize));
  }, [appendChunk, enqueue]);

  const ensureLoaded = useCallback(async (index: number) => {
    const current = progress.current;
    const generation = current.generation;
    const pending = () => generation === current.generation && index >= current.nextStart && !current.complete && !current.failed;
    while (pending()) {
      await enqueue((active) => {
        if (!pending()) return;
        const missing = index + 1 - current.nextStart;
        const limit = Math.min(catalogMaxRequestSize, Math.ceil(missing / catalogPageSize) * catalogPageSize);
        return appendChunk(active, limit);
      });
    }
    return generation === current.generation && index < current.nextStart;
  }, [appendChunk, enqueue]);

  const retry = useCallback(() => {
    const current = progress.current;
    if (!current.nextStart) { restart(false); return; }
    current.failed = false;
    void enqueue((generation) => appendChunk(generation, catalogPageSize));
  }, [appendChunk, enqueue, restart]);

  const refresh = useCallback(() => restart(true), [restart]);
  return { ...state, loadMore, ensureLoaded, refresh, retry };
}
