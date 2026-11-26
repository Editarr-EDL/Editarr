import { describe, expect, it } from "vitest";
import type { JellyfinMediaPage } from "../api/api-media-types";
import { cachedListingLimit, cachedPage, forgetLibraryPages, listingCacheKey, rememberPage } from "./library-page-cache";

const page = (total: number): JellyfinMediaPage => ({ items: [], total_count: total, rating_values: [], resolution_values: [] });

describe("library page cache", () => {
  it("keeps the pages of the most recently used listings only", () => {
    const cache = new Map<string, JellyfinMediaPage>();
    const listing = (ratingFilter: string) => listingCacheKey("movies", { ratingFilter });
    rememberPage(cache, listing("G"), { startIndex: 0, limit: 100 }, page(1));
    rememberPage(cache, listing("G"), { startIndex: 100, limit: 100 }, page(1));
    rememberPage(cache, listing("PG"), { startIndex: 0, limit: 100 }, page(2));
    rememberPage(cache, listing("R"), { startIndex: 0, limit: 100 }, page(3));
    expect(cachedPage(cache, listing("G"), { startIndex: 100, limit: 100 })?.total_count).toBe(1);
    rememberPage(cache, listing("NR"), { startIndex: 0, limit: 100 }, page(4));

    expect(cachedListingLimit).toBe(3);
    expect(cachedPage(cache, listing("PG"), { startIndex: 0, limit: 100 })).toBeUndefined();
    expect(cachedPage(cache, listing("G"), { startIndex: 0, limit: 100 })?.total_count).toBe(1);
    expect(cache.size).toBe(4);
    forgetLibraryPages(cache, "movies");
    expect(cache.size).toBe(0);
  });
});
