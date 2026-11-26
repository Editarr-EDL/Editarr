import { useCallback, useEffect, useRef, useState } from "react";
import { fetchLibraryItems } from "../api/api-service";
import type { JellyfinMediaItem, JellyfinMediaPage, LibraryItemQuery, LibraryJumpGroup } from "../api/api-media-types";
import { catalogMaxRequestSize, catalogPageSize, type LibraryListingQuery } from "./catalog-types";
import { cachedPage, forgetLibraryPages, listingCacheKey, rememberPage } from "./library-page-cache";

export interface LibraryListing {
  /** The loaded window of the listing, starting at `startIndex`. */
  items: JellyfinMediaItem[];
  startIndex: number;
  totalCount: number;
  ratingValues: string[];
  resolutionValues: string[];
  /** The listing's groups for the jump rail, in list order with each one's first index. */
  jumpGroups: LibraryJumpGroup[];
  /** The first page for the current query is loading; earlier results are cleared. */
  loading: boolean;
  /** A page after the window is loading. */
  loadingMore: boolean;
  /** A page before the window is loading. */
  loadingEarlier: boolean;
  error: string | null;
  /** Whether the window reaches the last matching item. */
  complete: boolean;
  /** Load the next page after any running request, unless nothing remains; repeated calls queue it once. */
  loadMore: () => void;
  /** Load the page before the window after any running request, unless the window starts the listing; queued once. */
  loadEarlier: () => void;
  /**
   * Make `index` part of the window with one request: by extending the window when it is within one
   * request's reach, otherwise by replacing the window with its page. Resolves "loaded", "failed" (the
   * window is unchanged and `retry` repeats the request) or "cancelled" when the query changed meanwhile.
   */
  reveal: (index: number) => Promise<"loaded" | "failed" | "cancelled">;
  /** Reload from the first page, asking the server to refresh its catalog. */
  refresh: () => void;
  /** Retry the request that failed: the first page, a page after or before the window, or a jump. */
  retry: () => void;
}

interface Progress {
  /** Changes with the query; a reveal begun for an older query reports nothing loaded. */
  query: number;
  /** Changes with the query and whenever the window starts over; late responses are dropped. */
  generation: number;
  items: JellyfinMediaItem[];
  start: number;
  end: number;
  complete: boolean;
  failed: boolean;
  busy: Promise<void> | null;
  /** Repeats the request that failed. */
  retryTask: (() => Promise<void>) | null;
  /** A next or previous page is already queued. */
  moreQueued: boolean;
  earlierQueued: boolean;
}

type Mode = "replace" | "append" | "prepend";

const emptyState = {
  items: [] as JellyfinMediaItem[], startIndex: 0, totalCount: 0, ratingValues: [] as string[], resolutionValues: [] as string[],
  jumpGroups: [] as LibraryJumpGroup[], loading: true, loadingMore: false, loadingEarlier: false,
  error: null as string | null, complete: false,
};

/** Start the window over at `start`, so responses for the old window are dropped. */
function resetWindow(current: Progress, start: number): void {
  Object.assign(current, {
    generation: current.generation + 1, items: [], start, end: start, complete: false, failed: false, retryTask: null,
    moreQueued: false, earlierQueued: false,
  });
}

/**
 * Load one library listing as a contiguous window of server pages: the first page for `query`, then pages
 * after or before it on request, or a new window around a far target, so a jump costs one request however
 * large the library. Changing the library or query clears the results (keeping the facet values) and starts
 * over, discarding late responses. Only one request runs at a time, so a page is never requested twice.
 * Pages without a title search are cached for the few most recent listings.
 */
export function useLibraryItems(libraryId: string, query: LibraryListingQuery, cache: Map<string, JellyfinMediaPage>,
  { onBeforePrepend }: { onBeforePrepend?: () => void } = {}): LibraryListing {
  const [state, setState] = useState(emptyState);
  const progress = useRef<Progress>({
    query: 0, generation: 0, items: [], start: 0, end: 0, complete: false, failed: false, retryTask: null, busy: null,
    moreQueued: false, earlierQueued: false,
  });
  const beforePrepend = useRef(onBeforePrepend);
  beforePrepend.current = onBeforePrepend;
  const { sort, direction, filter, ratingFilter, resolutionFilter, search } = query;

  const fetchChunk = useCallback(async (startIndex: number, limit: number, refresh: boolean) => {
    const request: LibraryItemQuery = {
      startIndex, limit, sortBy: sort, sortDirection: direction, edlFilter: filter, ratingFilter, resolutionFilter, search,
    };
    const listing = listingCacheKey(libraryId, request);
    const cacheable = !search.trim();
    if (refresh) forgetLibraryPages(cache, libraryId);
    else if (cacheable) {
      const cached = cachedPage(cache, listing, request);
      if (cached) return cached;
    }
    const page = await fetchLibraryItems(libraryId, { ...request, refresh });
    if (cacheable) rememberPage(cache, listing, request, page);
    return page;
  }, [cache, direction, filter, libraryId, ratingFilter, resolutionFilter, search, sort]);

  /** Run `task` after the running request, unless the window has started over by then. */
  const enqueue = useCallback((task: () => Promise<void> | void) => {
    const current = progress.current;
    const generation = current.generation;
    const run = (current.busy ?? Promise.resolve()).then(() => generation === current.generation ? task() : undefined);
    const tracked: Promise<void> = run.finally(() => { if (current.busy === tracked) current.busy = null; });
    current.busy = tracked;
    return tracked;
  }, []);

  const loadRange = useCallback(async (from: number, limit: number, mode: Mode, refresh = false) => {
    const current = progress.current;
    const generation = current.generation;
    setState((previous) => ({
      ...previous, loading: mode === "replace" && !current.items.length, loadingMore: mode === "append", loadingEarlier: mode === "prepend", error: null,
    }));
    try {
      const page = await fetchChunk(from, limit, refresh);
      if (generation !== current.generation) return;
      current.retryTask = null;
      const known = new Set(mode === "replace" ? [] : current.items.map((item) => item.id));
      const fresh = page.items.filter((item) => !known.has(item.id));
      if (mode === "replace") Object.assign(current, { items: fresh, start: from, end: from + page.items.length });
      else if (mode === "append") Object.assign(current, { items: [...current.items, ...fresh], end: from + page.items.length });
      else Object.assign(current, { items: [...fresh, ...current.items], start: from });
      current.complete = (mode !== "prepend" && !page.items.length) || current.end >= page.total_count;
      if (mode === "prepend") beforePrepend.current?.();
      setState({
        items: current.items,
        startIndex: current.start,
        totalCount: page.total_count,
        ratingValues: page.rating_values || [],
        resolutionValues: page.resolution_values || [],
        jumpGroups: page.jump_groups || [],
        loading: false,
        loadingMore: false,
        loadingEarlier: false,
        error: null,
        complete: current.complete,
      });
    } catch (reason) {
      if (generation !== current.generation) return;
      current.failed = true;
      current.retryTask = () => loadRange(from, limit, mode, refresh);
      setState((previous) => ({
        ...previous, loading: false, loadingMore: false, loadingEarlier: false,
        error: reason instanceof Error ? reason.message : "Unable to load library items.",
      }));
    }
  }, [fetchChunk]);

  const restart = useCallback((refresh: boolean) => {
    const current = progress.current;
    current.query += 1;
    resetWindow(current, 0);
    current.busy = null;
    setState((previous) => ({ ...emptyState, ratingValues: previous.ratingValues, resolutionValues: previous.resolutionValues }));
    void enqueue(() => loadRange(0, catalogPageSize, "replace", refresh));
  }, [enqueue, loadRange]);

  useEffect(() => { restart(false); }, [restart]);

  const loadMore = useCallback(() => {
    const current = progress.current;
    if (current.complete || current.failed || current.moreQueued) return;
    current.moreQueued = true;
    void enqueue(() => {
      current.moreQueued = false;
      if (!current.complete && !current.failed) return loadRange(current.end, catalogPageSize, "append");
    });
  }, [enqueue, loadRange]);

  const loadEarlier = useCallback(() => {
    const current = progress.current;
    if (!current.start || current.failed || current.earlierQueued) return;
    current.earlierQueued = true;
    void enqueue(() => {
      current.earlierQueued = false;
      if (!current.start || current.failed) return;
      const from = Math.max(0, current.start - catalogPageSize);
      return loadRange(from, current.start - from, "prepend");
    });
  }, [enqueue, loadRange]);

  const reveal = useCallback(async (index: number) => {
    const current = progress.current;
    const queryAtStart = current.query;
    const loaded = () => queryAtStart === current.query && index >= current.start && index < current.end;
    await enqueue(() => {
      if (loaded() || queryAtStart !== current.query) return;
      // Choosing a group is a fresh request, even after a failure.
      Object.assign(current, { failed: false, retryTask: null });
      if (index >= current.end && index < current.end + catalogMaxRequestSize && !current.complete) {
        const limit = Math.min(catalogMaxRequestSize, Math.ceil((index + 1 - current.end) / catalogPageSize) * catalogPageSize);
        return loadRange(current.end, limit, "append");
      }
      if (index < current.start && index >= current.start - catalogMaxRequestSize) {
        const from = Math.max(0, Math.floor(index / catalogPageSize) * catalogPageSize, current.start - catalogMaxRequestSize);
        return loadRange(from, current.start - from, "prepend");
      }
      // Too far to extend with one request: replace the window with the target's page once it arrives. Until
      // then (or if it fails) the current window stays, and requests queued for it are dropped. This task stays
      // the running request, so nothing else starts before the page arrives.
      Object.assign(current, { generation: current.generation + 1, moreQueued: false, earlierQueued: false });
      return loadRange(Math.floor(index / catalogPageSize) * catalogPageSize, catalogPageSize, "replace");
    });
    if (loaded()) return "loaded";
    return queryAtStart === current.query && current.failed ? "failed" : "cancelled";
  }, [enqueue, loadRange]);

  const retry = useCallback(() => {
    const current = progress.current;
    const task = current.retryTask;
    if (!task) return;
    Object.assign(current, { failed: false, retryTask: null });
    void enqueue(task);
  }, [enqueue]);

  const refresh = useCallback(() => restart(true), [restart]);
  return { ...state, loadMore, loadEarlier, reveal, refresh, retry };
}
