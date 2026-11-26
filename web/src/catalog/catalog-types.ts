export const catalogPageSize = 100;
/** The most items one catalog request may return; the server rejects larger limits. */
export const catalogMaxRequestSize = 500;

export type LibraryView = "icon" | "list";
export type SortKey = "title" | "year" | "rating" | "duration" | "type";
export type SortDirection = "asc" | "desc";
export type EdlFilter = "all" | "exported" | "not_exported" | "outdated";
export type RatingFilter = string;
export type ResolutionFilter = string;

export const librarySortLabels: Record<SortKey, string> = {
  title: "Title", year: "Year", rating: "Rating", duration: "Length", type: "Type",
};

export interface LibraryDisplayPreferences {
  view: LibraryView;
  sort: SortKey;
  direction: SortDirection;
  filter: EdlFilter;
  ratingFilter: RatingFilter;
  resolutionFilter: ResolutionFilter;
}

/** The server filters and order one continuous library listing is loaded with. */
export interface LibraryListingQuery {
  sort: SortKey;
  direction: SortDirection;
  filter: EdlFilter;
  ratingFilter: RatingFilter;
  resolutionFilter: ResolutionFilter;
  search: string;
}
