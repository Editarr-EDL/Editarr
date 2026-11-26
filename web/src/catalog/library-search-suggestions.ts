import type { JellyfinMediaItem } from "../api/api-media-types";
import type { EdlFilter } from "./catalog-types";

/** The facets a search token can set; each holds at most one value, like the catalog API. */
export interface LibraryFilters {
  filter: EdlFilter;
  ratingFilter: string;
  resolutionFilter: string;
}

export type FilterFacet = keyof LibraryFilters;

export interface SearchToken {
  facet: FilterFacet;
  value: string;
  label: string;
}

export type SearchSuggestion =
  | { kind: "filter"; id: string; label: string; token: SearchToken }
  | { kind: "title"; id: string; label: string; item: JellyfinMediaItem };

const edlLabels: Record<Exclude<EdlFilter, "all">, { token: string; option: string; terms: string }> = {
  exported: { token: "EDL exported", option: "EDL: Exported", terms: "edl exported has edl with edl" },
  not_exported: { token: "No EDL", option: "EDL: Not exported", terms: "no edl not exported without edl missing edl" },
  outdated: { token: "EDL outdated", option: "EDL: Outdated", terms: "edl outdated stale old edl out of date needs export" },
};

const maxFilterSuggestions = 8;
const maxTitleSuggestions = 5;

function normalized(value: string): string {
  return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().trim();
}

/** The tokens shown for the active filters, in a stable facet order. */
export function activeTokens(filters: LibraryFilters): SearchToken[] {
  const tokens: SearchToken[] = [];
  if (filters.filter !== "all") tokens.push({ facet: "filter", value: filters.filter, label: edlLabels[filters.filter].token });
  if (filters.ratingFilter !== "all") tokens.push({ facet: "ratingFilter", value: filters.ratingFilter, label: filters.ratingFilter });
  if (filters.resolutionFilter !== "all") tokens.push({ facet: "resolutionFilter", value: filters.resolutionFilter, label: filters.resolutionFilter });
  return tokens;
}

/**
 * Suggest filters and titles for the typed text. Filters come from the EDL states and the rating and
 * resolution values the library reports, matching by value or facet name ("rating" lists every rating);
 * values already chosen are left out. An empty query lists every filter. Titles are the first matches
 * already loaded for the same text.
 * @param text The text typed into the search box.
 * @param titleMatches Loaded items the server returned for `text`, or none while that search is pending.
 */
export function searchSuggestions(
  text: string,
  filters: LibraryFilters,
  values: { ratings: string[]; resolutions: string[] },
  titleMatches: JellyfinMediaItem[],
): SearchSuggestion[] {
  const query = normalized(text);
  const matches = (value: string, facetTerms: string) => !query || normalized(value).includes(query) || normalized(facetTerms).startsWith(query);
  const filterSuggestions: SearchSuggestion[] = [];
  for (const value of ["exported", "not_exported", "outdated"] as const) {
    const labels = edlLabels[value];
    if (filters.filter !== value && (matches(labels.option, "edl") || normalized(labels.terms).includes(query))) {
      // "edl" alone, "no edl", "not exported", "exported" and "outdated" all find the matching state.
      filterSuggestions.push({ kind: "filter", id: `edl-${value}`, label: labels.option, token: { facet: "filter", value, label: labels.token } });
    }
  }
  for (const value of values.ratings) {
    if (filters.ratingFilter !== value && matches(value, "rating")) {
      filterSuggestions.push({ kind: "filter", id: `rating-${value}`, label: `Rating: ${value}`, token: { facet: "ratingFilter", value, label: value } });
    }
  }
  for (const value of values.resolutions) {
    if (filters.resolutionFilter !== value && matches(value, "resolution")) {
      filterSuggestions.push({ kind: "filter", id: `resolution-${value}`, label: `Resolution: ${value}`, token: { facet: "resolutionFilter", value, label: value } });
    }
  }
  const titles: SearchSuggestion[] = query
    ? titleMatches.slice(0, maxTitleSuggestions).map((item) => ({ kind: "title", id: `title-${item.id}`, label: item.name, item }))
    : [];
  return [...titles, ...filterSuggestions.slice(0, maxFilterSuggestions)];
}
