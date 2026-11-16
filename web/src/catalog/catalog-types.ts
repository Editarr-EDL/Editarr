export const catalogPageSize = 100;

export type LibraryView = "icon" | "list";
export type SortKey = "title" | "year" | "rating" | "duration" | "type";
export type EdlFilter = "all" | "exported" | "not_exported";
export type RatingFilter = string;
export type ResolutionFilter = string;

export interface LibraryDisplayPreferences {
  view: LibraryView;
  sort: SortKey;
  direction: "asc" | "desc";
  filter: EdlFilter;
  ratingFilter: RatingFilter;
  resolutionFilter: ResolutionFilter;
}
