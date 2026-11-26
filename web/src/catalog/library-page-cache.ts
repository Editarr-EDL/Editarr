import type { JellyfinMediaPage, LibraryItemQuery } from "../api/api-media-types";

/** Library listings whose pages stay cached; older listings are dropped. */
export const cachedListingLimit = 3;

/** The listing a request belongs to: library, order, filters and search, without its range. */
export function listingCacheKey(libraryId: string, query: LibraryItemQuery): string {
  return JSON.stringify({
    libraryId,
    sortBy: query.sortBy ?? "title",
    sortDirection: query.sortDirection ?? "asc",
    edlFilter: query.edlFilter ?? "all",
    ratingFilter: query.ratingFilter ?? "all",
    resolutionFilter: query.resolutionFilter ?? "all",
    search: query.search?.trim() || "",
  });
}

function pageKey(listing: string, query: LibraryItemQuery): string {
  return `${listing}|${query.startIndex ?? 0}:${query.limit ?? 100}`;
}

/** A cached page, marking its listing as recently used. */
export function cachedPage(cache: Map<string, JellyfinMediaPage>, listing: string, query: LibraryItemQuery): JellyfinMediaPage | undefined {
  const key = pageKey(listing, query);
  const page = cache.get(key);
  if (page) { cache.delete(key); cache.set(key, page); }
  return page;
}

/** Cache a page, keeping only the pages of the most recently used listings. */
export function rememberPage(cache: Map<string, JellyfinMediaPage>, listing: string, query: LibraryItemQuery, page: JellyfinMediaPage): void {
  const key = pageKey(listing, query);
  cache.delete(key);
  cache.set(key, page);
  const recent = [...new Set([...cache.keys()].reverse().map((entry) => entry.slice(0, entry.lastIndexOf("|"))))].slice(0, cachedListingLimit);
  for (const entry of [...cache.keys()]) {
    if (!recent.includes(entry.slice(0, entry.lastIndexOf("|")))) cache.delete(entry);
  }
}

/** Drop every cached page of one library. */
export function forgetLibraryPages(cache: Map<string, JellyfinMediaPage>, libraryId: string): void {
  for (const key of [...cache.keys()]) {
    if (key.includes(`"libraryId":${JSON.stringify(libraryId)}`)) cache.delete(key);
  }
}
